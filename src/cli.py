"""CLI entrypoint: run the agent on one real issue by hand.

Runs the Agent Core and GitHub client end to end against a local clone,
without any webhook or queue infrastructure.

Usage:
    python -m src.cli --clone /path/to/local/clone --issue 12
    python -m src.cli --clone /path/to/local/clone --issue 12 --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from pathlib import Path

import structlog

from src.agent.core import AgentLoop
from src.agent.llm import LLMClient
from src.agent.state import AgentStatus, Job
from src.agent.tools import default_registry
from src.agent.workspace import LocalWorkspace
from src.config import load_settings
from src.github.client import GitHubClient
from src.github.git_ops import create_commit_and_push

logger = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the issue agent on one GitHub issue.")
    parser.add_argument("--clone", required=True, help="Path to a local clone of the target repo")
    parser.add_argument("--issue", required=True, type=int, help="Issue number to fix")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run investigation/implementation/validation but do not push a branch or open a PR",
    )
    args = parser.parse_args()

    settings = load_settings()
    github = GitHubClient(token=settings.github_token, repo_full_name=settings.github_repo)

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

    # Work on a disposable copy of the clone so re-runs always start clean.
    work_dir = Path(args.clone).resolve().parent / f".issue-agent-work-{job.id}"
    shutil.copytree(args.clone, work_dir)  # includes .git so branch/commit/push works
    workspace = LocalWorkspace(work_dir)

    llm = LLMClient(model=settings.claude_model)
    loop = AgentLoop(llm=llm, tools=default_registry())

    logger.info("agent loop starting", job_id=job.id, issue=job.issue_number)
    result = loop.run(job, workspace)

    if not result.success:
        logger.warning("escalating", job_id=job.id, reason=job.escalation_reason)
        if not args.dry_run:
            github.comment_on_issue(
                job.issue_number,
                f"🤖 I attempted this issue but couldn't reach a validated fix.\n\n{job.escalation_reason}",
            )
        print(f"ESCALATED: {job.escalation_reason}")
        sys.exit(1)

    print(f"VALIDATED FIX:\n{result.summary}\n")
    print(workspace.run(["git", "diff"]).stdout)

    if args.dry_run:
        print("(dry run — not pushing a branch or opening a PR)")
        return

    branch_name = f"issue-agent/issue-{job.issue_number}"
    commit_message = f"Fix #{job.issue_number}: {job.issue_title}"
    create_commit_and_push(
        workspace, branch_name, commit_message, settings.github_token, settings.github_repo
    )

    pr_body = (
        f"Fixes #{job.issue_number}\n\n"
        f"## Summary\n{result.summary}\n\n"
        f"## Validation\nAll checks passed (pytest, ruff, black, mypy) after "
        f"{len(job.attempts)} attempt(s)."
    )
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
