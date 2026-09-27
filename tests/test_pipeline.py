from types import SimpleNamespace

import pytest

from src import pipeline
from src.agent.state import Clarification, IssueComment
from src.config import Settings
from src.github.client import ExistingPR, IssueContext
from src.pipeline import Outcome, run_issue


class FakeGitHub:
    def __init__(self, issue: IssueContext, existing: ExistingPR | None = None):
        self.issue = issue
        self.existing = existing
        self.comments: list[tuple[int, str]] = []
        self.acknowledged: list[int] = []

    def get_issue(self, number):
        return self.issue

    def find_open_agent_pr(self, number):
        return self.existing

    def acknowledge(self, number):
        self.acknowledged.append(number)

    def comment_on_issue(self, number, body, kind="note"):
        self.comments.append((number, body, kind))


def make_issue(**overrides) -> IssueContext:
    base = {"number": 5, "title": "t", "body": "b", "comments": []}
    return IssueContext(**{**base, **overrides})


@pytest.fixture
def settings():
    return Settings(
        _env_file=None, github_token="t", github_repo="o/r", anthropic_api_key="k", workspaces_dir="ws"
    )


def wire(monkeypatch, github: FakeGitHub, triage=lambda llm, job: None):
    monkeypatch.setattr(pipeline, "resolve_auth", lambda s, repo: SimpleNamespace(token="tok", identity="test"))
    monkeypatch.setattr(pipeline, "GitHubClient", lambda token, repo_full_name: github)
    monkeypatch.setattr(pipeline, "LLMClient", lambda **kwargs: object())
    monkeypatch.setattr(pipeline, "triage_issue", triage)

    def no_clone(*args, **kwargs):
        raise AssertionError("must not clone: the run should have ended earlier")

    monkeypatch.setattr(pipeline, "clone_repo", no_clone)


def run(settings, dry_run=False, **kwargs):
    messages: list[str] = []
    result = run_issue(settings, 5, dry_run=dry_run, emit=messages.append, **kwargs)
    return result, messages


def test_a_pull_request_is_skipped(monkeypatch, settings):
    github = FakeGitHub(make_issue(is_pull_request=True))
    wire(monkeypatch, github)
    result, _ = run(settings)
    assert result.outcome == Outcome.SKIPPED
    assert github.comments == []


def test_a_closed_issue_is_skipped(monkeypatch, settings):
    wire(monkeypatch, FakeGitHub(make_issue(state="closed")))
    result, messages = run(settings)
    assert result.outcome == Outcome.SKIPPED
    assert "closed" in messages[0]


def test_an_issue_with_an_open_agent_pr_is_skipped(monkeypatch, settings):
    github = FakeGitHub(make_issue(), existing=ExistingPR(number=9, url="https://x/pull/9"))
    wire(monkeypatch, github)
    result, _ = run(settings)
    assert result.outcome == Outcome.SKIPPED
    assert result.pr_url == "https://x/pull/9"
    assert github.acknowledged == []


def test_a_vague_issue_is_answered_before_any_cloning(monkeypatch, settings):
    github = FakeGitHub(make_issue())
    wire(monkeypatch, github, triage=lambda llm, job: Clarification("Which currencies?", "unclear"))
    result, _ = run(settings)

    assert result.outcome == Outcome.NEEDS_CLARIFICATION
    assert github.acknowledged == [5]
    assert len(github.comments) == 1
    assert "Which currencies?" in github.comments[0][1]
    assert github.comments[0][2] == "clarification"


def test_a_dry_run_touches_nothing_on_github(monkeypatch, settings):
    github = FakeGitHub(make_issue())
    wire(monkeypatch, github, triage=lambda llm, job: Clarification("Which currencies?", "unclear"))
    result, _ = run(settings, dry_run=True)

    assert result.outcome == Outcome.NEEDS_CLARIFICATION
    assert github.acknowledged == []
    assert github.comments == []


def test_workspaces_are_deleted_after_a_real_run_but_kept_for_dry_runs(monkeypatch, settings, tmp_path):
    def fake_run(settings, issue_number, dry_run, emit, job_dirs, trigger, force):
        job_dir = tmp_path / ("dry" if dry_run else "real")
        job_dir.mkdir()
        (job_dir / "repo").mkdir()
        job_dirs.append(job_dir)
        return pipeline.RunResult(Outcome.VALIDATED)

    monkeypatch.setattr(pipeline, "_run", fake_run)

    run_issue(settings, 5, dry_run=False)
    run_issue(settings, 5, dry_run=True)
    assert not (tmp_path / "real").exists()
    assert (tmp_path / "dry").exists()


def test_keep_workspaces_overrides_cleanup(monkeypatch, tmp_path):
    kept = Settings(_env_file=None, github_token="t", github_repo="o/r", keep_workspaces=True)

    def fake_run(settings, issue_number, dry_run, emit, job_dirs, trigger, force):
        (tmp_path / "job").mkdir()
        job_dirs.append(tmp_path / "job")
        return pipeline.RunResult(Outcome.PR_OPENED)

    monkeypatch.setattr(pipeline, "_run", fake_run)
    run_issue(kept, 5, dry_run=False)
    assert (tmp_path / "job").exists()


def test_workspaces_are_deleted_even_when_the_run_crashes(monkeypatch, settings, tmp_path):
    def crashing_run(settings, issue_number, dry_run, emit, job_dirs, trigger, force):
        (tmp_path / "job").mkdir()
        job_dirs.append(tmp_path / "job")
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "_run", crashing_run)
    with pytest.raises(RuntimeError):
        run_issue(settings, 5, dry_run=False)
    assert not (tmp_path / "job").exists()


AGENT_ASKED = IssueComment("bot[bot]", "Which currencies?", agent_kind="clarification")
HUMAN_ANSWERED = IssueComment("ann", "USD and EUR, symbols only.")


def test_a_label_on_an_issue_the_agent_already_answered_is_skipped_cheaply(monkeypatch, settings):
    github = FakeGitHub(make_issue(comments=[AGENT_ASKED]))
    calls = []
    wire(monkeypatch, github, triage=lambda llm, job: calls.append("triage"))
    result, messages = run(settings)

    assert result.outcome == Outcome.SKIPPED
    assert "reply on the issue" in messages[0]
    assert calls == []  # no model call was made
    assert github.comments == []
    assert github.acknowledged == []


def test_force_overrides_the_conversation_check(monkeypatch, settings):
    github = FakeGitHub(make_issue(comments=[AGENT_ASKED]))
    wire(monkeypatch, github, triage=lambda llm, job: Clarification("Which currencies?", "still unclear"))
    result, _ = run(settings, force=True)
    assert result.outcome == Outcome.NEEDS_CLARIFICATION


def test_a_reply_is_skipped_when_the_agent_was_not_waiting_for_one(monkeypatch, settings):
    github = FakeGitHub(make_issue(comments=[IssueComment("ann", "any update?")]))
    wire(monkeypatch, github)
    result, messages = run(settings, trigger="reply")
    assert result.outcome == Outcome.SKIPPED
    assert "isn't waiting" in messages[0]


def test_a_reply_to_a_clarification_resumes_the_run(monkeypatch, settings):
    github = FakeGitHub(make_issue(comments=[AGENT_ASKED, HUMAN_ANSWERED]))
    seen = {}

    def triage(llm, job):
        seen["context"] = job.issue_context()
        return Clarification("Still unclear?", "x")  # stop right after triage; we only inspect what it saw

    wire(monkeypatch, github, triage=triage)
    result, _ = run(settings, trigger="reply")

    assert result.outcome == Outcome.NEEDS_CLARIFICATION  # got past the skip check and reached triage
    assert "(you, the agent, earlier): Which currencies?" in seen["context"]
    assert "@ann: USD and EUR, symbols only." in seen["context"]
