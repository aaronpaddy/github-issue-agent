"""CLI entrypoint: run the agent on one real issue by hand.

Clones the target repo fresh from GitHub, builds its environment, runs the
agent loop, and opens a PR if the fix validates. No webhook or queue involved.

Usage:
    python -m src.cli --issue 12
    python -m src.cli --issue 12 --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path

import structlog

from src.agent.core import AgentLoop
from src.agent.cost import CostTracker
from src.agent.environment import create_python_env
from src.agent.llm import LLMClient
from src.agent.state import AgentStatus, Job
from src.agent.tools import default_registry
from src.agent.validator import Validator
from src.agent.workspace import LocalWorkspace
from src.config import load_settings
from src.github.auth import resolve_auth
from src.github.client import GitHubClient
from src.github.git_ops import clone_repo, create_commit_and_push

logger = structlog.get_logger(__name__)


_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)


def build_pr_body(issue_number: int, summary: str, attempts: int) -> str:
    summary = summary.strip()
    # The model often writes its own headings, sometimes after a line of chatter. If it did,
    # keep everything from its first heading; otherwise supply one.
    heading = _HEADING.search(summary)
    summary = summary[heading.start() :] if heading else f"## Summary\n{summary}"
    return (
        f"Fixes #{issue_number}\n\n"
        f"{summary}\n\n"
        "## Validation\n"
        "No new issues from pytest, ruff, black, or mypy compared with the base branch, "
        f"after {attempts} attempt(s)."
    )


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

    job = Job(
        id=str(uuid.uuid4()),
        repo=settings.github_repo,
        issue_number=issue.number,
        issue_title=issue.title,
        issue_body=issue.body,
        issue_comments=issue.comments,
        max_attempts=settings.max_attempts,
    )

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

    cost = CostTracker(model=settings.claude_model, budget_usd=settings.max_budget_usd)
    loop = AgentLoop(
        llm=LLMClient(model=settings.claude_model, api_key=settings.anthropic_api_key or None, cost=cost),
        tools=default_registry(),
        validate_fn=validator.validate,
    )

    logger.info("agent loop starting", job_id=job.id, issue=job.issue_number)
    result = loop.run(job, workspace)
    print(f"API usage: {cost.summary()}")

    if not result.success:
        logger.warning("escalating", job_id=job.id)
        if not args.dry_run:
            github.comment_on_issue(
                job.issue_number,
                "I attempted this issue but couldn't reach a validated fix.\n\n"
                f"{job.escalation_reason}",
            )
        print(f"ESCALATED: {job.escalation_reason}")
        sys.exit(1)

    print(f"VALIDATED FIX:\n{result.summary}\n")
    print(workspace.run(["git", "diff"]).stdout)

    if args.dry_run:
        print(f"(dry run: not pushing. Workspace kept at {repo_dir})")
        return

    branch_name = f"issue-agent/issue-{job.issue_number}"
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

    pr_body = build_pr_body(job.issue_number, result.summary, len(job.attempts))
    pr_url = github.open_pull_request(
        branch=branch_name,
        title=commit_message,
        body=pr_body,
        base=github.default_branch(),
    )
    job.pr_url = pr_url
    job.status = AgentStatus.DONE
    print(f"PR opened: {pr_url}")


if __name__ == "__main__":
    main()
