"""Git plumbing: clone a clean checkout, then commit and push a validated fix.

This is application code, not a model tool: it runs outside the run_command
allowlist, which only constrains what the *model* can execute.

The GitHub token is passed to git through environment config rather than
embedded in a URL, so it never appears in argv, error messages, or the
clone's stored remote.
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

from src.agent.workspace import LocalWorkspace

# Test/lint caches are produced by validation runs and must never be committed,
# whatever the target repo's own .gitignore says. Written to .git/info/exclude,
# which is local to the clone.
LOCAL_EXCLUDES = ("__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", "*.egg-info/")


class GitOpsError(Exception):
    pass


def _auth_env(token: str) -> dict[str, str]:
    credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credentials}",
    }


def _git(argv: list[str], cwd: Path | None, token: str | None = None) -> str:
    env = _auth_env(token) if token else None
    proc = subprocess.run(
        ["git", *argv], cwd=cwd, capture_output=True, text=True, check=False, env=env
    )
    if proc.returncode != 0:
        raise GitOpsError(f"`git {' '.join(argv)}` failed: {proc.stderr.strip()}")
    return proc.stdout


def clone_repo(repo_full_name: str, dest: Path, token: str) -> None:
    """Clone the repo's default branch fresh from GitHub into dest."""
    _git(["clone", "--quiet", f"https://github.com/{repo_full_name}.git", str(dest)], None, token)
    exclude = dest / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as f:
        f.write("\n".join(LOCAL_EXCLUDES) + "\n")


def create_commit_and_push(
    workspace: LocalWorkspace,
    branch_name: str,
    commit_message: str,
    token: str,
    repo_full_name: str,
    author_name: str,
    author_email: str,
) -> None:
    root = workspace.root
    _git(["checkout", "-b", branch_name], root)
    _git(["add", "-A"], root)
    _git(
        [
            "-c", f"user.email={author_email}",
            "-c", f"user.name={author_name}",
            "commit", "-m", commit_message,
        ],
        root,
    )
    _git(["push", f"https://github.com/{repo_full_name}.git", f"HEAD:{branch_name}"], root, token)
