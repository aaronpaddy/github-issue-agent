"""Git plumbing for turning a validated workspace diff into a pushed branch.

This is application code, not a model tool — it runs after validation has
already passed, so it isn't subject to the run_command allowlist (that
allowlist constrains what the *model* can execute mid-investigation, not
what the trusted CLI/worker does once a fix is confirmed).
"""

from __future__ import annotations

import subprocess

from src.agent.workspace import LocalWorkspace


class GitOpsError(Exception):
    pass


def _run(workspace: LocalWorkspace, argv: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(argv, cwd=workspace.root, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise GitOpsError(f"`{' '.join(argv)}` failed: {proc.stderr.strip()}")
    return proc


def create_commit_and_push(
    workspace: LocalWorkspace,
    branch_name: str,
    commit_message: str,
    token: str,
    repo_full_name: str,
) -> None:
    _run(workspace, ["git", "checkout", "-b", branch_name])
    _run(workspace, ["git", "add", "-A"])
    _run(workspace, ["git", "-c", "user.email=agent@issue-agent.local", "-c", "user.name=Issue Agent",
                      "commit", "-m", commit_message])

    push_url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"
    _run(workspace, ["git", "push", push_url, f"HEAD:{branch_name}"])
