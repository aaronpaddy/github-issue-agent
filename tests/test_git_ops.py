import subprocess

from src.agent.workspace import LocalWorkspace
from src.github.git_ops import diff_stats


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True)


def test_diff_stats_counts_modified_and_new_files(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")

    (tmp_path / "a.py").write_text("one\nTWO\nthree\nfour\n")  # +2 -1
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x\ny\n")  # new, +2

    stats = diff_stats(LocalWorkspace(tmp_path))

    assert set(stats.files) == {"a.py", "tests/test_a.py"}
    assert stats.lines_added == 4
    assert stats.lines_deleted == 1
    assert stats.changed_lines == 5


def test_diff_stats_of_an_unchanged_repo_is_empty(tmp_path):
    (tmp_path / "a.py").write_text("one\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")

    stats = diff_stats(LocalWorkspace(tmp_path))
    assert stats.files == ()
    assert stats.changed_lines == 0
