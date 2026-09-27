"""Run a repository's code inside a throwaway Docker container.

Installing a project's dependencies and running its tests executes code the agent has no reason
to trust: a build script, a conftest, a test. So every command that runs repository code goes
through `DockerWorkspace`, and only the clone and its virtualenv are visible inside.

What the container can and cannot do:
  - sees only the clone (`/workspace`) and its virtualenv (`/venv`); nothing else of the host
  - receives none of the host's environment, so no API keys or tokens
  - has `.git` mounted read-only, so code cannot plant a git config or hook that the host's
    own git would later execute
  - has no network, except while installing dependencies
  - runs as an unprivileged user with every capability dropped, a read-only root filesystem, and
    caps on memory, CPU and process count
  - is deleted when the command ends, and killed if it overruns its timeout

Files are still read and written on the host (through the path-jailed Workspace), since that
touches nothing executable; `git` also runs on the host, hardened, because the container has
neither git nor any business changing history.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.agent.workspace import CommandResult, LocalWorkspace

CONTAINER_REPO = "/workspace"
CONTAINER_VENV = "/venv"

# Docker Desktop's file sharing can show a container a stale copy of a file the host wrote in the
# last few tenths of a second (measured: about 1 in 4 runs with no wait, none after 0.5s). Waiting
# a second after any host-side write means the container always runs against what was written.
FILE_SYNC_SETTLE_SECONDS = 1.0


class SandboxError(Exception):
    pass


@dataclass(frozen=True)
class SandboxConfig:
    image: str = "python:3.12-slim"
    memory: str = "2g"
    cpus: str = "2"
    pids_limit: int = 512
    tmp_size: str = "1g"


def docker_problem() -> str | None:
    """None if Docker is usable, otherwise a message saying why not."""
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except FileNotFoundError:
        return "Docker is not installed (the `docker` command was not found)."
    except subprocess.TimeoutExpired:
        return "Docker did not respond; is Docker Desktop running?"
    if proc.returncode != 0:
        return "Docker is installed but not running. Start Docker Desktop and try again."
    return None


def build_docker_command(
    *,
    name: str,
    config: SandboxConfig,
    repo_dir: Path,
    venv_dir: Path,
    argv: list[str],
    network: bool,
    user: str,
) -> list[str]:
    """The exact `docker run` invocation for one sandboxed command."""
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "bridge" if network else "none",
        "--user",
        user,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,exec,size={config.tmp_size}",
        "--memory",
        config.memory,
        "--memory-swap",
        config.memory,
        "--cpus",
        config.cpus,
        "--pids-limit",
        str(config.pids_limit),
        "-v",
        f"{repo_dir}:{CONTAINER_REPO}",
        "-v",
        f"{venv_dir}:{CONTAINER_VENV}",
    ]
    if (repo_dir / ".git").exists():
        command += ["-v", f"{repo_dir / '.git'}:{CONTAINER_REPO}/.git:ro"]
    command += [
        "-w",
        CONTAINER_REPO,
        "-e",
        "HOME=/tmp",
        "-e",
        f"VIRTUAL_ENV={CONTAINER_VENV}",
        "-e",
        f"PATH={CONTAINER_VENV}/bin:/usr/local/bin:/usr/bin:/bin",
        "-e",
        "PIP_CACHE_DIR=/tmp/pip-cache",
        "-e",
        "PIP_DISABLE_PIP_VERSION_CHECK=1",
        config.image,
        *argv,
    ]
    return command


class DockerWorkspace(LocalWorkspace):
    """A Workspace whose commands run in a container. File access is unchanged."""

    def __init__(self, root: str | Path, venv_dir: str | Path, config: SandboxConfig | None = None):
        super().__init__(root)
        self.venv_dir = Path(venv_dir).resolve()
        self.venv_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or SandboxConfig()
        self._last_change = float("-inf")

    def mark_changed(self) -> None:
        """Record that files were just changed on the host, outside write_file."""
        self._last_change = time.monotonic()

    def write_file(self, relative_path: str, content: str) -> None:
        super().write_file(relative_path, content)
        self.mark_changed()

    def _wait_for_file_sync(self) -> None:
        remaining = FILE_SYNC_SETTLE_SECONDS - (time.monotonic() - self._last_change)
        if remaining > 0:
            time.sleep(remaining)

    def run(self, argv: list[str], timeout: int = 120, network: bool = False) -> CommandResult:
        if argv and argv[0] == "git":
            return self._run_git_on_host(argv, timeout)

        self._wait_for_file_sync()
        name = f"issue-agent-{uuid.uuid4().hex[:12]}"
        command = build_docker_command(
            name=name,
            config=self.config,
            repo_dir=self.root,
            venv_dir=self.venv_dir,
            argv=argv,
            network=network,
            user=f"{os.getuid()}:{os.getgid()}",
        )
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as e:
            # Stopping the docker client does not stop the container; remove it explicitly.
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
            stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            return CommandResult(124, stdout, f"command timed out after {timeout}s")
        except FileNotFoundError:
            raise SandboxError("Docker is not installed (the `docker` command was not found).") from None
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def _run_git_on_host(self, argv: list[str], timeout: int) -> CommandResult:
        # `core.fsmonitor` and hooks are the ways a repo's own config can make git run programs.
        hardened = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *argv[1:]]
        return super().run(hardened, timeout)
