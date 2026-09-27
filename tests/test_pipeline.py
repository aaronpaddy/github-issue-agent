from types import SimpleNamespace

import pytest

from src import pipeline
from src.agent.state import Clarification, IssueComment
from src.config import Settings
from src.github.client import ExistingPR, IssueContext
from src.pipeline import Outcome, ask_or_give_up, run_issue


class FakeGitHub:
    def __init__(self, issue: IssueContext, existing: ExistingPR | None = None):
        self.issue = issue
        self.existing = existing
        self.comments: list[tuple[int, str]] = []
        self.acknowledged: list[int] = []
        self.acknowledged_comments: list[tuple[int, int]] = []

    def get_issue(self, number):
        return self.issue

    def find_open_agent_pr(self, number):
        return self.existing

    def acknowledge(self, number):
        self.acknowledged.append(number)

    def acknowledge_comment(self, issue_number, comment_id):
        self.acknowledged_comments.append((issue_number, comment_id))

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


def question() -> Clarification:
    return Clarification("Which currencies?", "unclear")


def test_a_question_is_posted_while_under_the_cap():
    github = FakeGitHub(make_issue())
    outcome = ask_or_give_up(github, 5, question(), rounds=2, max_rounds=3, dry_run=False)
    assert outcome == Outcome.NEEDS_CLARIFICATION
    assert [c[2] for c in github.comments] == ["clarification"]


def test_at_the_cap_it_posts_a_final_comment_instead_of_another_question():
    github = FakeGitHub(make_issue())
    outcome = ask_or_give_up(github, 5, question(), rounds=3, max_rounds=3, dry_run=False)
    assert outcome == Outcome.GAVE_UP
    assert [c[2] for c in github.comments] == ["gave-up"]
    assert "3 times" in github.comments[0][1]


def test_a_dry_run_posts_nothing_either_way():
    github = FakeGitHub(make_issue())
    assert ask_or_give_up(github, 5, question(), 0, 3, dry_run=True) == Outcome.NEEDS_CLARIFICATION
    assert ask_or_give_up(github, 5, question(), 3, 3, dry_run=True) == Outcome.GAVE_UP
    assert github.comments == []


def three_rounds_thread():
    thread = []
    for i in range(3):
        thread += [
            IssueComment("bot[bot]", f"q{i}", agent_kind="clarification", id=100 + i),
            IssueComment("ann", f"a{i}", id=200 + i),
        ]
    return thread


def test_a_fourth_vague_answer_makes_the_run_give_up(monkeypatch, settings):
    github = FakeGitHub(make_issue(comments=three_rounds_thread()))
    wire(monkeypatch, github, triage=lambda llm, job: question())
    result, messages = run(settings, trigger="reply")

    assert result.outcome == Outcome.GAVE_UP
    assert github.comments[-1][2] == "gave-up"
    assert messages[-1].startswith("GAVE UP")


def test_a_clear_answer_after_the_last_allowed_question_still_gets_a_chance(monkeypatch, settings):
    # Three questions were asked, but triage is now satisfied: the run must proceed, not give up.
    github = FakeGitHub(make_issue(comments=three_rounds_thread()))
    proceeded = []

    def triage_passes(llm, job):
        proceeded.append(True)

    def no_env(*args, **kwargs):
        raise RuntimeError("reached cloning")  # proves we got past triage

    wire(monkeypatch, github, triage=triage_passes)
    monkeypatch.setattr(pipeline, "clone_repo", no_env)
    with pytest.raises(RuntimeError, match="reached cloning"):
        run(settings, trigger="reply")
    assert proceeded == [True]
    assert github.comments == []


def test_a_reply_run_reacts_to_the_answer_not_the_issue(monkeypatch, settings):
    thread = [IssueComment("bot[bot]", "q", agent_kind="clarification", id=1), IssueComment("ann", "a", id=77)]
    github = FakeGitHub(make_issue(comments=thread))
    wire(monkeypatch, github, triage=lambda llm, job: question())
    run(settings, trigger="reply")

    assert github.acknowledged_comments == [(5, 77)]
    assert github.acknowledged == []


def test_a_label_run_reacts_to_the_issue(monkeypatch, settings):
    github = FakeGitHub(make_issue())
    wire(monkeypatch, github, triage=lambda llm, job: question())
    run(settings, trigger="label")

    assert github.acknowledged == [5]
    assert github.acknowledged_comments == []


def test_a_dry_run_reacts_to_nothing(monkeypatch, settings):
    thread = [IssueComment("bot[bot]", "q", agent_kind="clarification", id=1), IssueComment("ann", "a", id=77)]
    github = FakeGitHub(make_issue(comments=thread))
    wire(monkeypatch, github, triage=lambda llm, job: question())
    run(settings, dry_run=True, trigger="reply")
    assert github.acknowledged == []
    assert github.acknowledged_comments == []
