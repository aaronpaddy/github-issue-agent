"""Workspace: the sandboxed filesystem view every tool operates through.

All tool code talks to a Workspace, never to the filesystem directly. This
keeps two things centralized: path jailing (no tool can read/write outside
the repo root, even if the model tries `../../etc/passwd`) and swappability
(a Docker-backed Workspace can replace LocalWorkspace later without touching
any tool code).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class PathEscapeError(Exception):
    """Raised when a requested path resolves outside the workspace root."""


class Workspace:
    """Interface for a sandboxed repo checkout that tools can act on."""

    root: Path

    def resolve(self, relative_path: str) -> Path:
        raise NotImplementedError

    def list_files(self, relative_path: str = ".") -> list[str]:
        raise NotImplementedError

    def read_file(self, relative_path: str) -> str:
        raise NotImplementedError

    def write_file(self, relative_path: str, content: str) -> None:
        raise NotImplementedError

    def run(self, argv: list[str], timeout: int = 120) -> CommandResult:
        raise NotImplementedError


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class LocalWorkspace(Workspace):
    """Operates directly on a local directory (a real clone on disk).

    No sandboxing beyond path jailing, so only suitable for human-supervised
    runs. Unattended execution should use an isolated (e.g. containerized)
    Workspace implementation instead.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"workspace root does not exist: {self.root}")

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise PathEscapeError(
                f"path '{relative_path}' resolves outside workspace root"
            ) from None
        return candidate

    def list_files(self, relative_path: str = ".") -> list[str]:
        base = self.resolve(relative_path)
        if not base.exists():
            return []
        results = []
        for p in sorted(base.rglob("*")):
            if any(part in {".git", "__pycache__", ".venv", "venv", "node_modules"} for part in p.parts):
                continue
            if p.is_file():
                results.append(str(p.relative_to(self.root)))
        return results

    def read_file(self, relative_path: str) -> str:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8", errors="replace")

    def write_file(self, relative_path: str, content: str) -> None:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def run(self, argv: list[str], timeout: int = 120) -> CommandResult:
        try:
            proc = subprocess.run(
                argv,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return CommandResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            return CommandResult(
                returncode=124,
                stdout=stdout,
                stderr=f"command timed out after {timeout}s",
            )
