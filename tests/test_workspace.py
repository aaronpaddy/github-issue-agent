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


def test_run_executes_in_root(ws):
    result = ws.run(["python3", "-c", "print('ran')"])
    assert result.ok
    assert "ran" in result.stdout
