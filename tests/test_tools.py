import pytest

from src.agent.tools import default_registry
from src.agent.workspace import LocalWorkspace


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    return LocalWorkspace(tmp_path)


@pytest.fixture
def registry():
    return default_registry()


def test_read_file_via_registry(registry, ws):
    result = registry.execute(ws, "read_file", {"path": "a.py"})
    assert result.ok
    assert "def foo" in result.output


def test_edit_file_requires_unique_match(registry, ws):
    ws.write_file("dup.py", "x = 1\nx = 1\n")
    result = registry.execute(ws, "edit_file", {"path": "dup.py", "old_string": "x = 1", "new_string": "x = 2"})
    assert not result.ok
    assert "matches 2 locations" in result.error


def test_edit_file_success(registry, ws):
    result = registry.execute(
        ws, "edit_file", {"path": "a.py", "old_string": "return 1", "new_string": "return 2"}
    )
    assert result.ok
    assert ws.read_file("a.py") == "def foo():\n    return 2\n"


def test_create_file_fails_if_exists(registry, ws):
    result = registry.execute(ws, "create_file", {"path": "a.py", "content": "x"})
    assert not result.ok


def test_unknown_tool(registry, ws):
    result = registry.execute(ws, "delete_repo", {})
    assert not result.ok
    assert "unknown tool" in result.error


def test_run_command_rejects_non_allowlisted(registry, ws):
    result = registry.execute(ws, "run_command", {"argv": ["rm", "-rf", "/"]})
    assert not result.ok
    assert "not allowlisted" in result.error


def test_run_command_rejects_shell_metacharacters(registry, ws):
    result = registry.execute(ws, "run_command", {"argv": ["git", "status", "; rm -rf /"]})
    assert not result.ok


def test_run_command_allows_allowlisted_git_subcommand(registry, ws):
    result = registry.execute(ws, "run_command", {"argv": ["git", "status"]})
    assert result.ok


def test_run_command_rejects_disallowed_git_subcommand(registry, ws):
    result = registry.execute(ws, "run_command", {"argv": ["git", "push"]})
    assert not result.ok
