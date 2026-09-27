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

from src.agent.sandbox import DockerWorkspace
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


def create_sandboxed_python_env(workspace: DockerWorkspace, extras: str = "dev") -> None:
    """Build the project's virtualenv inside the container.

    Installing runs the project's build backend, which is the first point where its code
    executes, so it happens in the sandbox too. It is the only step given network access.
    """
    steps: list[tuple[list[str], bool]] = [
        (["python", "-m", "venv", "/venv"], False),
        (["pip", "install", "--quiet", "-e", f".[{extras}]" if extras else "."], True),
        (["pip", "install", "--quiet", *VALIDATION_TOOLS], True),
    ]
    for argv, needs_network in steps:
        result = workspace.run(argv, timeout=INSTALL_TIMEOUT_SECONDS, network=needs_network)
        if not result.ok:
            tail = (result.stdout + result.stderr).strip()[-2000:]
            raise EnvironmentSetupError(f"`{' '.join(argv)}` failed in the sandbox:\n{tail}")
