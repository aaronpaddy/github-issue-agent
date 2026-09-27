"""One end-to-end run on one issue: the shared code path for the CLI and the queue worker.

    skip checks -> triage -> clone -> build env -> baseline -> agent loop -> policy -> PR/comment

It reports what happened as a RunResult and narrates through `emit`, so each caller decides
how to show it (the CLI prints; the worker logs).
"""

from __future__ import annotations

import enum
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from src.agent.core import AgentLoop
from src.agent.cost import CostTracker
from src.agent.environment import create_python_env, create_sandboxed_python_env
from src.agent.llm import LLMClient
from src.agent.policy import Action, decide
from src.agent.sandbox import DockerWorkspace, SandboxConfig, SandboxError, docker_problem
from src.agent.state import AgentStatus, Clarification, Job
from src.agent.tools import default_registry
from src.agent.triage import triage_issue
from src.agent.validator import Validator
from src.agent.workspace import LocalWorkspace
from src.config import Settings
from src.github.auth import resolve_auth
from src.github.client import GitHubClient
from src.github.git_ops import clone_repo, create_commit_and_push, diff_stats
from src.github.messages import (
    build_pr_body,
    clarification_comment,
    declined_comment,
    escalation_comment,
    gave_up_comment,
    no_change_comment,
)
from src.github.threads import (
    CLARIFICATION,
    DECLINED,
    ESCALATED,
    GAVE_UP,
    NO_CHANGE,
    clarification_rounds,
    skip_reason,
)

logger = structlog.get_logger(__name__)

Emit = Callable[[str], None]


class Outcome(str, enum.Enum):
    SKIPPED = "skipped"
    NEEDS_CLARIFICATION = "needs_clarification"
    GAVE_UP = "gave_up"  # asked as many times as allowed and it is still too vague
    NO_CHANGE_NEEDED = "no_change_needed"
    ESCALATED = "escalated"
    DECLINED = "declined"  # a validated change, but not confident enough to open a PR
    VALIDATED = "validated"  # dry run: a PR would have been opened
    PR_OPENED = "pr_opened"
    DRAFT_PR_OPENED = "draft_pr_opened"


@dataclass
class RunResult:
    outcome: Outcome
    detail: str = ""
    pr_url: str | None = None
    spent_usd: float = 0.0
    workspace: Path | None = None


def ask_or_give_up(
    github: GitHubClient,
    issue_number: int,
    clarification: Clarification,
    rounds: int,
    max_rounds: int,
    dry_run: bool,
) -> Outcome:
    """Post the clarification question, or, once the agent has already asked `max_rounds` times,
    a final comment saying it is stopping. The cap applies when asking, not when resuming, so an
    answer to the last allowed question still gets a fair chance to make the issue actionable."""
    give_up = rounds >= max_rounds
    if not dry_run:
        if give_up:
            github.comment_on_issue(issue_number, gave_up_comment(clarification, rounds), GAVE_UP)
        else:
            github.comment_on_issue(
                issue_number, clarification_comment(clarification), CLARIFICATION
            )
    return Outcome.GAVE_UP if give_up else Outcome.NEEDS_CLARIFICATION


def run_issue(
    settings: Settings,
    issue_number: int,
    *,
    dry_run: bool = False,
    emit: Emit = print,
    trigger: str = "label",
    force: bool = False,
) -> RunResult:
    """Run the agent on one issue. A real run deletes its clone and virtualenv when it ends
    (a long-lived worker would otherwise fill the disk); a dry run keeps them so the result
    can be inspected, as does KEEP_WORKSPACES."""
    job_dirs: list[Path] = []
    try:
        return _run(settings, issue_number, dry_run, emit, job_dirs, trigger, force)
    finally:
        if not dry_run and not settings.keep_workspaces:
            for job_dir in job_dirs:
                shutil.rmtree(job_dir, ignore_errors=True)


def _run(
    settings: Settings,
    issue_number: int,
    dry_run: bool,
    emit: Emit,
    job_dirs: list[Path],
    trigger: str,
    force: bool,
) -> RunResult:
    repo = settings.github_repo
    auth = resolve_auth(settings, repo)
    logger.info("authenticated", identity=auth.identity)
    github = GitHubClient(token=auth.token, repo_full_name=repo)

    logger.info("fetching issue", repo=repo, issue=issue_number)
    issue = github.get_issue(issue_number)

    if issue.is_pull_request:
        emit(f"SKIPPED: #{issue.number} is a pull request, not an issue.")
        return RunResult(Outcome.SKIPPED, "is a pull request")
    if issue.state != "open":
        emit(f"SKIPPED: issue #{issue.number} is {issue.state}.")
        return RunResult(Outcome.SKIPPED, f"issue is {issue.state}")
    reason = None if force else skip_reason(trigger, issue.comments)
    if reason:
        emit(f"SKIPPED: {reason}.")
        return RunResult(Outcome.SKIPPED, reason)

    existing = github.find_open_agent_pr(issue.number)
    if existing:
        emit(f"SKIPPED: issue #{issue.number} already has an open agent PR: {existing.url}")
        return RunResult(Outcome.SKIPPED, "already has an open agent PR", pr_url=existing.url)

    if not dry_run:
        # On a reply, mark the answer itself as seen; otherwise mark the issue.
        answer = next((c for c in reversed(issue.comments) if not c.is_agent), None)
        if trigger == "reply" and answer is not None and answer.id is not None:
            github.acknowledge_comment(issue.number, answer.id)
        else:
            github.acknowledge(issue.number)

    job = Job(
        id=str(uuid.uuid4()),
        repo=repo,
        issue_number=issue.number,
        issue_title=issue.title,
        issue_body=issue.body,
        issue_comments=issue.comments,
        max_attempts=settings.max_attempts,
    )

    if settings.sandbox == "docker":
        problem = docker_problem()
        if problem:  # fail closed: never quietly run untrusted code on the host instead
            raise SandboxError(f"{problem} Refusing to run repository code outside a sandbox.")

    cost = CostTracker(model=settings.claude_model, budget_usd=settings.max_budget_usd)
    llm = LLMClient(model=settings.claude_model, api_key=settings.anthropic_api_key or None, cost=cost)

    rounds = clarification_rounds(issue.comments)
    vague = triage_issue(llm, job)
    if vague:
        logger.info("triage: needs clarification", job_id=job.id, rounds=rounds)
        outcome = ask_or_give_up(
            github, job.issue_number, vague, rounds, settings.max_clarification_rounds, dry_run
        )
        label = "GAVE UP" if outcome == Outcome.GAVE_UP else "NEEDS CLARIFICATION"
        emit(f"API usage: {cost.summary()}")
        emit(f"{label} (before any work): {vague.question}\n\n{vague.findings}")
        return RunResult(outcome, vague.question, spent_usd=cost.spent_usd)

    job_dir = Path(settings.workspaces_dir).resolve() / job.id
    repo_dir = job_dir / "repo"
    job_dir.mkdir(parents=True)
    job_dirs.append(job_dir)

    logger.info("cloning", repo=repo, dest=str(repo_dir))
    clone_repo(repo, repo_dir, auth.token)
    workspace: LocalWorkspace
    if settings.sandbox == "docker":
        workspace = DockerWorkspace(
            repo_dir, job_dir / "venv", SandboxConfig(image=settings.sandbox_image)
        )
        logger.info("installing project environment in the sandbox (this can take a minute)")
        create_sandboxed_python_env(workspace)
    else:
        workspace = LocalWorkspace(repo_dir)
        logger.info("installing project environment on the host (SANDBOX=none)")
        create_python_env(workspace, job_dir / "venv")

    validator = Validator()
    logger.info("capturing baseline validation")
    baseline = validator.capture_baseline(workspace)
    for name, res in baseline.items():
        logger.info("baseline", check=name, passed=res.passed, issues=sum(res.issues.values()))

    loop = AgentLoop(llm=llm, tools=default_registry(), validate_fn=validator.validate)
    logger.info("agent loop starting", job_id=job.id, issue=job.issue_number)
    result = loop.run(job, workspace)
    emit(f"API usage: {cost.summary()}")

    def done(outcome: Outcome, detail: str = "", pr_url: str | None = None) -> RunResult:
        return RunResult(outcome, detail, pr_url, spent_usd=cost.spent_usd, workspace=repo_dir)

    if job.status == AgentStatus.NEEDS_CLARIFICATION and job.clarification:
        logger.info("asking for clarification", job_id=job.id, rounds=rounds)
        outcome = ask_or_give_up(
            github,
            job.issue_number,
            job.clarification,
            rounds,
            settings.max_clarification_rounds,
            dry_run,
        )
        label = "GAVE UP" if outcome == Outcome.GAVE_UP else "NEEDS CLARIFICATION"
        emit(f"{label}: {job.clarification.question}\n\n{job.clarification.findings}")
        return done(outcome, job.clarification.question)

    if job.status == AgentStatus.NO_CHANGE_NEEDED and job.no_change:
        logger.info("no change needed", job_id=job.id)
        if not dry_run:
            github.comment_on_issue(job.issue_number, no_change_comment(job.no_change), NO_CHANGE)
        emit(f"NO CHANGE NEEDED: {job.no_change.reason}\n\n{job.no_change.evidence}")
        return done(Outcome.NO_CHANGE_NEEDED, job.no_change.reason)

    if not result.success:
        logger.warning("escalating", job_id=job.id)
        if not dry_run:
            github.comment_on_issue(
                job.issue_number, escalation_comment(job.escalation_reason or ""), ESCALATED
            )
        emit(f"ESCALATED: {job.escalation_reason}")
        return done(Outcome.ESCALATED, job.escalation_reason or "")

    stats = diff_stats(workspace)
    earlier_failures = [
        [name for name, ok in attempt.validation.checks.items() if not ok]
        for attempt in job.attempts[:-1]
        if attempt.validation
    ]
    decision = decide(job.assessment, earlier_failures, stats)
    assessment = job.assessment
    emit(f"VALIDATED FIX:\n{result.summary}\n")
    if assessment:
        emit(f"Self-assessment: {assessment.confidence} confidence. {assessment.interpretation}")
        for assumption in assessment.assumptions:
            emit(f"  assumption: {assumption}")
    emit(
        f"Policy decision: {decision.action.value} "
        f"({len(stats.files)} files, {stats.changed_lines} changed lines)"
    )
    for reason in decision.reasons:
        emit(f"  - {reason}")
    emit(workspace.run(["git", "diff", "--cached"]).stdout)

    if decision.action == Action.COMMENT_ONLY:
        if not dry_run:
            github.comment_on_issue(
                job.issue_number, declined_comment(assessment, decision), DECLINED
            )
        emit("(no pull request opened)")
        return done(Outcome.DECLINED, "; ".join(decision.reasons))

    overlaps = github.overlapping_prs(stats.files)
    for overlap in overlaps:
        emit(f"Overlaps with open PR #{overlap.number} on: {', '.join(overlap.files)}")

    if dry_run:
        emit(f"(dry run: not pushing. Workspace kept at {repo_dir})")
        return done(Outcome.VALIDATED)

    branch_name = github.branch_name_for(job.issue_number, job.id)
    commit_message = f"Fix #{job.issue_number}: {job.issue_title}"
    create_commit_and_push(
        workspace,
        branch_name,
        commit_message,
        auth.token,
        repo,
        author_name=auth.commit_name,
        author_email=auth.commit_email,
    )

    pr_body = build_pr_body(
        job.issue_number,
        assessment.summary if assessment and assessment.summary else result.summary,
        len(job.attempts),
        assessment=assessment,
        decision=decision,
        overlaps=overlaps,
    )
    draft = decision.action == Action.OPEN_DRAFT_PR
    pr_url = github.open_pull_request(
        branch=branch_name,
        title=commit_message,
        body=pr_body,
        base=github.default_branch(),
        draft=draft,
    )
    job.pr_url = pr_url
    job.status = AgentStatus.DONE
    emit(f"{'Draft PR' if draft else 'PR'} opened: {pr_url}")
    return done(Outcome.DRAFT_PR_OPENED if draft else Outcome.PR_OPENED, pr_url=pr_url)
