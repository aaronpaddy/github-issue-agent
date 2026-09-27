"""CLI entrypoint: run the agent on one real issue by hand.

Clones the target repo fresh from GitHub, builds its environment, runs the
agent loop, and opens a PR if the fix validates. No webhook or queue involved.

Usage:
    python -m src.cli --issue 12
    python -m src.cli --issue 12 --dry-run
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

import structlog

from src.agent.core import AgentLoop
from src.agent.cost import CostTracker
from src.agent.environment import create_python_env
from src.agent.llm import LLMClient
from src.agent.policy import Action, decide
from src.agent.state import AgentStatus, Job
from src.agent.tools import default_registry
from src.agent.triage import triage_issue
from src.agent.validator import Validator
from src.agent.workspace import LocalWorkspace
from src.config import load_settings
from src.github.auth import resolve_auth
from src.github.client import GitHubClient
from src.github.git_ops import clone_repo, create_commit_and_push, diff_stats
from src.github.messages import (
    build_pr_body,
    clarification_comment,
    declined_comment,
    escalation_comment,
    no_change_comment,
)

logger = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the issue agent on one GitHub issue.")
    parser.add_argument("--issue", required=True, type=int, help="Issue number to fix")
    parser.add_argument("--repo", help="Target repository as owner/name (overrides GITHUB_REPO)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full loop but do not push a branch, open a PR, or comment on the issue",
    )
    args = parser.parse_args()

    settings = load_settings()
    if args.repo:
        settings.github_repo = args.repo
    auth = resolve_auth(settings, settings.github_repo)
    logger.info("authenticated", identity=auth.identity)
    github = GitHubClient(token=auth.token, repo_full_name=settings.github_repo)

    logger.info("fetching issue", repo=settings.github_repo, issue=args.issue)
    issue = github.get_issue(args.issue)

    if issue.is_pull_request:
        print(f"SKIPPED: #{issue.number} is a pull request, not an issue.")
        return
    if issue.state != "open":
        print(f"SKIPPED: issue #{issue.number} is {issue.state}.")
        return

    existing = github.find_open_agent_pr(issue.number)
    if existing:
        print(f"SKIPPED: issue #{issue.number} already has an open agent PR: {existing.url}")
        return

    job = Job(
        id=str(uuid.uuid4()),
        repo=settings.github_repo,
        issue_number=issue.number,
        issue_title=issue.title,
        issue_body=issue.body,
        issue_comments=issue.comments,
        max_attempts=settings.max_attempts,
    )

    cost = CostTracker(model=settings.claude_model, budget_usd=settings.max_budget_usd)
    llm = LLMClient(model=settings.claude_model, api_key=settings.anthropic_api_key or None, cost=cost)

    vague = triage_issue(llm, job)
    if vague:
        logger.info("triage: needs clarification", job_id=job.id)
        if not args.dry_run:
            github.comment_on_issue(job.issue_number, clarification_comment(vague))
        print(f"API usage: {cost.summary()}")
        print(f"NEEDS CLARIFICATION (before any work): {vague.question}\n\n{vague.findings}")
        return

    job_dir = Path(settings.workspaces_dir).resolve() / job.id
    repo_dir = job_dir / "repo"
    job_dir.mkdir(parents=True)

    logger.info("cloning", repo=settings.github_repo, dest=str(repo_dir))
    clone_repo(settings.github_repo, repo_dir, auth.token)
    workspace = LocalWorkspace(repo_dir)

    logger.info("installing project environment (this can take a minute)")
    create_python_env(workspace, job_dir / "venv")

    validator = Validator()
    logger.info("capturing baseline validation")
    baseline = validator.capture_baseline(workspace)
    for name, res in baseline.items():
        logger.info("baseline", check=name, passed=res.passed, issues=sum(res.issues.values()))

    loop = AgentLoop(
        llm=llm,
        tools=default_registry(),
        validate_fn=validator.validate,
    )

    logger.info("agent loop starting", job_id=job.id, issue=job.issue_number)
    result = loop.run(job, workspace)
    print(f"API usage: {cost.summary()}")

    if job.status == AgentStatus.NEEDS_CLARIFICATION and job.clarification:
        logger.info("asking for clarification", job_id=job.id)
        if not args.dry_run:
            github.comment_on_issue(job.issue_number, clarification_comment(job.clarification))
        print(f"NEEDS CLARIFICATION: {job.clarification.question}\n\n{job.clarification.findings}")
        return

    if job.status == AgentStatus.NO_CHANGE_NEEDED and job.no_change:
        logger.info("no change needed", job_id=job.id)
        if not args.dry_run:
            github.comment_on_issue(job.issue_number, no_change_comment(job.no_change))
        print(f"NO CHANGE NEEDED: {job.no_change.reason}\n\n{job.no_change.evidence}")
        return

    if not result.success:
        logger.warning("escalating", job_id=job.id)
        if not args.dry_run:
            github.comment_on_issue(job.issue_number, escalation_comment(job.escalation_reason or ""))
        print(f"ESCALATED: {job.escalation_reason}")
        sys.exit(1)

    stats = diff_stats(workspace)
    decision = decide(job.assessment, len(job.attempts), stats)
    assessment = job.assessment
    print(f"VALIDATED FIX:\n{result.summary}\n")
    if assessment:
        print(f"Self-assessment: {assessment.confidence} confidence. {assessment.interpretation}")
        for assumption in assessment.assumptions:
            print(f"  assumption: {assumption}")
    print(
        f"Policy decision: {decision.action.value} "
        f"({len(stats.files)} files, {stats.changed_lines} changed lines)"
    )
    for reason in decision.reasons:
        print(f"  - {reason}")
    print(workspace.run(["git", "diff", "--cached"]).stdout)

    if decision.action == Action.COMMENT_ONLY:
        if not args.dry_run:
            github.comment_on_issue(job.issue_number, declined_comment(assessment, decision))
        print("(no pull request opened)")
        return

    overlaps = github.overlapping_prs(stats.files)
    for overlap in overlaps:
        print(f"Overlaps with open PR #{overlap.number} on: {', '.join(overlap.files)}")

    if args.dry_run:
        print(f"(dry run: not pushing. Workspace kept at {repo_dir})")
        return

    branch_name = github.branch_name_for(job.issue_number, job.id)
    commit_message = f"Fix #{job.issue_number}: {job.issue_title}"
    create_commit_and_push(
        workspace,
        branch_name,
        commit_message,
        auth.token,
        settings.github_repo,
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
    pr_url = github.open_pull_request(
        branch=branch_name,
        title=commit_message,
        body=pr_body,
        base=github.default_branch(),
        draft=decision.action == Action.OPEN_DRAFT_PR,
    )
    job.pr_url = pr_url
    job.status = AgentStatus.DONE
    kind = "Draft PR" if decision.action == Action.OPEN_DRAFT_PR else "PR"
    print(f"{kind} opened: {pr_url}")


if __name__ == "__main__":
    main()
