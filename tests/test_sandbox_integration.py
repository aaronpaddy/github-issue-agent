"""These run real containers and try to break out of them. Skipped when Docker or the image
is unavailable (docker pull python:3.12-slim to enable them)."""

import os
import subprocess

import pytest

from src.agent.sandbox import DockerWorkspace, SandboxConfig, docker_problem

IMAGE = SandboxConfig().image


def _image_present() -> bool:
    if docker_problem() is not None:
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False).returncode == 0


pytestmark = pytest.mark.skipif(not _image_present(), reason=f"Docker and {IMAGE} are required")


@pytest.fixture
def ws(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return DockerWorkspace(repo, tmp_path / "venv")


def test_it_runs_as_the_unprivileged_host_user_not_root(ws):
    result = ws.run(["id", "-u"])
    assert result.stdout.strip() == str(os.getuid())
    assert result.stdout.strip() != "0"


def test_files_written_in_the_container_appear_on_the_host(ws):
    result = ws.run(["sh", "-c", "echo hello > /workspace/out.txt"])
    assert result.ok
    assert (ws.root / "out.txt").read_text().strip() == "hello"


def test_there_is_no_network(ws):
    code = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"
    assert not ws.run(["python", "-c", code], timeout=30).ok


def test_the_hosts_environment_does_not_leak_in(ws, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "hunter2")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    result = ws.run(["env"])
    assert "hunter2" not in result.stdout
    assert "sk-ant-fake" not in result.stdout
    assert "SUPER_SECRET_TOKEN" not in result.stdout


def test_the_rest_of_the_hosts_filesystem_is_invisible(ws, tmp_path):
    outside = tmp_path.parent
    result = ws.run(["python", "-c", f"import os, sys; sys.exit(0 if os.path.exists({str(outside)!r}) else 1)"])
    assert result.returncode == 1  # the host path does not exist inside the container


def test_git_metadata_is_read_only_inside_the_container(ws):
    git_dir = ws.root / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")

    result = ws.run(["sh", "-c", "echo 'fsmonitor = evil' >> /workspace/.git/config"])

    assert not result.ok
    assert (git_dir / "config").read_text() == "[core]\n"


def test_the_root_filesystem_is_read_only(ws):
    assert not ws.run(["sh", "-c", "touch /etc/planted"]).ok


def test_an_overrunning_command_is_killed_and_its_container_removed(ws):
    result = ws.run(["sleep", "60"], timeout=3)
    assert result.returncode == 124
    running = subprocess.run(
        ["docker", "ps", "-q", "--filter", "name=issue-agent-"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert running.stdout.strip() == ""


def test_git_works_even_though_the_image_has_no_git(ws):
    subprocess.run(["git", "init", "-q"], cwd=ws.root, check=True)
    (ws.root / "a.txt").write_text("x")
    result = ws.run(["git", "status", "--short"])
    assert result.ok
    assert "a.txt" in result.stdout


def test_a_failing_command_reports_its_exit_code_and_output(ws):
    result = ws.run(["sh", "-c", "echo oops >&2; exit 3"])
    assert result.returncode == 3
    assert "oops" in result.stderr
