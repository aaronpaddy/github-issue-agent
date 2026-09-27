"""CLI entrypoint: run the agent on one real issue by hand.

Clones the target repo fresh from GitHub, builds its environment, runs the
agent loop, and opens a PR if the fix validates. See src/pipeline.py for the run itself;
the webhook service in src/service uses the same pipeline.

Usage:
    python -m src.cli --issue 12
    python -m src.cli --issue 12 --dry-run
"""

from __future__ import annotations

import argparse
import sys

from src.config import load_settings
from src.pipeline import Outcome, run_issue


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the issue agent on one GitHub issue.")
    parser.add_argument("--issue", required=True, type=int, help="Issue number to fix")
    parser.add_argument("--repo", help="Target repository as owner/name (overrides GITHUB_REPO)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full loop but do not push a branch, open a PR, or comment on the issue",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if the agent already had the last word on the issue",
    )
    args = parser.parse_args()

    settings = load_settings()
    if args.repo:
        settings.github_repo = args.repo

    result = run_issue(settings, args.issue, dry_run=args.dry_run, emit=print, force=args.force)
    if result.outcome == Outcome.ESCALATED:
        sys.exit(1)


if __name__ == "__main__":
    main()
