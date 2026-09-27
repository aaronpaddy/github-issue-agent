import pytest

from src.agent.workspace import LocalWorkspace, PathEscapeError


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")
    return LocalWorkspace(tmp_path)


def test_read_write_roundtrip(ws):
    ws.write_file("src/new.py", "x = 1\n")
    assert ws.read_file("src/new.py") == "x = 1\n"


def test_list_files_excludes_vcs_dirs(ws, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("junk")
    files = ws.list_files()
    assert "src/main.py" in files
    assert not any(".git" in f for f in files)


def test_path_escape_blocked(ws):
    with pytest.raises(PathEscapeError):
        ws.resolve("../../etc/passwd")


def test_read_missing_file_raises(ws):
    with pytest.raises(FileNotFoundError):
        ws.read_file("nope.py")


def test_run_applies_env_overrides(tmp_path):
    ws = LocalWorkspace(tmp_path, env={"AGENT_TEST_VAR": "from-workspace"})
    result = ws.run(["python3", "-c", "import os; print(os.environ['AGENT_TEST_VAR'])"])
    assert result.ok
    assert "from-workspace" in result.stdout


def test_run_executes_in_root(ws):
    result = ws.run(["python3", "-c", "print('ran')"])
    assert result.ok
    assert "ran" in result.stdout


def test_writes_inside_dot_git_are_rejected(ws, tmp_path):
    (tmp_path / ".git").mkdir()
    for target in (".git/config", ".git/hooks/pre-commit", "sub/.git/config"):
        with pytest.raises(PathEscapeError, match=".git"):
            ws.write_file(target, "[core]\nfsmonitor = evil")
    assert not (tmp_path / ".git" / "config").exists()


def test_files_that_merely_contain_git_in_the_name_are_still_writable(ws):
    ws.write_file(".gitignore", "x\n")
    ws.write_file("docs/.github/workflows/ci.yml", "y\n")
    assert ws.read_file(".gitignore") == "x\n"
