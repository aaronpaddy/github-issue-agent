import subprocess
from pathlib import Path

import pytest

from src.agent.sandbox import (
    DockerWorkspace,
    SandboxConfig,
    SandboxError,
    build_docker_command,
    docker_problem,
)


def command(tmp_path: Path, **overrides) -> list[str]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    venv = tmp_path / "venv"
    venv.mkdir(exist_ok=True)
    kwargs = {
        "name": "issue-agent-test",
        "config": SandboxConfig(),
        "repo_dir": repo,
        "venv_dir": venv,
        "argv": ["pytest", "-q"],
        "network": False,
        "user": "501:20",
    }
    return build_docker_command(**{**kwargs, **overrides})


def pairs(cmd: list[str], flag: str) -> list[str]:
    return [cmd[i + 1] for i, part in enumerate(cmd) if part == flag]


def test_there_is_no_network_by_default_and_bridge_only_when_asked(tmp_path):
    assert pairs(command(tmp_path), "--network") == ["none"]
    assert pairs(command(tmp_path, network=True), "--network") == ["bridge"]


def test_the_container_is_locked_down(tmp_path):
    cmd = command(tmp_path)
    assert pairs(cmd, "--cap-drop") == ["ALL"]
    assert pairs(cmd, "--security-opt") == ["no-new-privileges"]
    assert "--read-only" in cmd
    assert "--privileged" not in cmd
    assert pairs(cmd, "--user") == ["501:20"]
    assert "--rm" in cmd


def test_resource_limits_are_set(tmp_path):
    cmd = command(tmp_path, config=SandboxConfig(memory="1g", cpus="1", pids_limit=64))
    assert pairs(cmd, "--memory") == ["1g"]
    assert pairs(cmd, "--memory-swap") == ["1g"]  # no swap, so the memory cap is real
    assert pairs(cmd, "--cpus") == ["1"]
    assert pairs(cmd, "--pids-limit") == ["64"]


def test_only_the_clone_and_venv_are_mounted_and_git_is_read_only(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    cmd = command(tmp_path)
    mounts = pairs(cmd, "-v")
    assert mounts == [
        f"{tmp_path / 'repo'}:/workspace",
        f"{tmp_path / 'venv'}:/venv",
        f"{tmp_path / 'repo' / '.git'}:/workspace/.git:ro",
    ]


def test_no_git_mount_when_the_repo_has_no_git_dir(tmp_path):
    assert len(pairs(command(tmp_path), "-v")) == 2


def test_no_host_environment_is_passed_in(tmp_path):
    cmd = command(tmp_path)
    assert "--env-file" not in cmd
    keys = sorted(v.split("=")[0] for v in pairs(cmd, "-e"))
    assert keys == ["HOME", "PATH", "PIP_CACHE_DIR", "PIP_DISABLE_PIP_VERSION_CHECK", "VIRTUAL_ENV"]


def test_the_image_and_command_come_last(tmp_path):
    cmd = command(tmp_path, config=SandboxConfig(image="python:3.11-slim"), argv=["ruff", "check", "."])
    assert cmd[-4:] == ["python:3.11-slim", "ruff", "check", "."]


class Recorder:
    def __init__(self, raises=None):
        self.calls: list[list[str]] = []
        self.raises = raises

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.raises and argv[:2] == ["docker", "run"]:
            raise self.raises
        return subprocess.CompletedProcess(argv, 0, stdout="out", stderr="")


def make_workspace(tmp_path) -> DockerWorkspace:
    repo = tmp_path / "repo"
    repo.mkdir()
    return DockerWorkspace(repo, tmp_path / "venv")


def test_commands_run_in_a_container_but_git_runs_on_the_host(monkeypatch, tmp_path):
    rec = Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    ws = make_workspace(tmp_path)

    ws.run(["pytest", "-q"])
    ws.run(["git", "status", "--short"])

    assert rec.calls[0][:2] == ["docker", "run"]
    assert rec.calls[1][0] == "git"
    assert "docker" not in rec.calls[1]


def test_host_git_ignores_the_repos_own_fsmonitor_and_hooks(monkeypatch, tmp_path):
    rec = Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    make_workspace(tmp_path).run(["git", "diff"])
    assert rec.calls[0] == [
        "git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "diff",
    ]


def test_an_overrunning_command_has_its_container_removed(monkeypatch, tmp_path):
    rec = Recorder(raises=subprocess.TimeoutExpired(cmd="docker", timeout=1))
    monkeypatch.setattr(subprocess, "run", rec)

    result = make_workspace(tmp_path).run(["sleep", "99"], timeout=1)

    assert result.returncode == 124
    assert "timed out" in result.stderr
    name = rec.calls[0][rec.calls[0].index("--name") + 1]
    assert ["docker", "rm", "-f", name] in rec.calls


def test_a_missing_docker_binary_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", Recorder(raises=FileNotFoundError()))
    with pytest.raises(SandboxError, match="not installed"):
        make_workspace(tmp_path).run(["pytest"])


def test_docker_problem_reports_each_failure_mode(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError()

    def stopped(argv, **k):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="cannot connect")

    def hung(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=20)

    def fine(argv, **k):
        return subprocess.CompletedProcess(argv, 0, stdout="29.8.0", stderr="")

    monkeypatch.setattr(subprocess, "run", missing)
    assert "not installed" in (docker_problem() or "")
    monkeypatch.setattr(subprocess, "run", stopped)
    assert "not running" in (docker_problem() or "")
    monkeypatch.setattr(subprocess, "run", hung)
    assert "respond" in (docker_problem() or "")
    monkeypatch.setattr(subprocess, "run", fine)
    assert docker_problem() is None


def test_a_container_waits_for_the_file_share_after_a_host_write(monkeypatch, tmp_path):
    from src.agent import sandbox

    sleeps = []
    monkeypatch.setattr(sandbox.time, "sleep", sleeps.append)
    clock = iter([100.0, 100.2, 105.0, 105.0])  # write, then run 0.2s later, then a quiet run
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(subprocess, "run", Recorder())
    ws = make_workspace(tmp_path)

    ws.write_file("a.py", "x = 1\n")   # monotonic -> 100.0
    ws.run(["pytest"])                    # monotonic -> 100.2: 0.8s short of the settle time
    ws.run(["pytest"])                    # monotonic -> 105.0: long since settled

    assert sleeps == [pytest.approx(0.8)]


def test_git_does_not_wait_because_it_runs_on_the_host(monkeypatch, tmp_path):
    from src.agent import sandbox

    sleeps = []
    monkeypatch.setattr(sandbox.time, "sleep", sleeps.append)
    monkeypatch.setattr(subprocess, "run", Recorder())
    ws = make_workspace(tmp_path)
    ws.mark_changed()
    ws.run(["git", "status"])
    assert sleeps == []


def test_mark_changed_covers_edits_made_outside_write_file(monkeypatch, tmp_path):
    from src.agent import sandbox

    sleeps = []
    monkeypatch.setattr(sandbox.time, "sleep", sleeps.append)
    monkeypatch.setattr(subprocess, "run", Recorder())
    ws = make_workspace(tmp_path)
    ws.mark_changed()
    ws.run(["pytest"])
    assert len(sleeps) == 1 and sleeps[0] > 0
