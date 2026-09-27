"""Builds the Python environment a target repository's checks run in.

Validation must run against the target project's own dependencies, not this
agent's. The venv lives outside the workspace root so it can never be picked
up by `git add -A`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.agent.workspace import LocalWorkspace

INSTALL_TIMEOUT_SECONDS = 900
# The validator runs these, so they must exist even if the project's own extras omit them.
VALIDATION_TOOLS = ("pytest", "ruff", "black", "mypy")


class EnvironmentSetupError(Exception):
    pass


def _run(argv: list[str], cwd: Path) -> None:
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise EnvironmentSetupError(f"`{' '.join(argv)}` timed out") from None
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip()[-2000:]
        raise EnvironmentSetupError(f"`{' '.join(argv)}` failed:\n{tail}")


def create_python_env(workspace: LocalWorkspace, venv_dir: Path, extras: str = "dev") -> None:
    """Create a venv, install the workspace project (editable, with extras),
    and point the workspace's commands at it."""
    venv_dir = venv_dir.resolve()
    _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=workspace.root)

    bin_dir = venv_dir / "bin"
    target = f".[{extras}]" if extras else "."
    pip = str(bin_dir / "pip")
    _run([pip, "install", "--quiet", "-e", target], cwd=workspace.root)
    _run([pip, "install", "--quiet", *VALIDATION_TOOLS], cwd=workspace.root)

    workspace.env = {
        "VIRTUAL_ENV": str(venv_dir),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
