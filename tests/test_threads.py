from src.agent.state import IssueComment
from src.github.threads import ERROR, parse_comment, skip_reason, tag


def agent(kind: str) -> IssueComment:
    return IssueComment("bot[bot]", "x", agent_kind=kind)


HUMAN = IssueComment("ann", "hello")


def test_tagging_then_parsing_round_trips():
    comment = parse_comment("bot[bot]", tag("What do you mean?", "clarification"))
    assert comment.body == "What do you mean?"
    assert comment.agent_kind == "clarification"
    assert comment.is_agent


def test_an_ordinary_comment_has_no_kind():
    comment = parse_comment("ann", "  Just a note.  ")
    assert comment.body == "Just a note."
    assert comment.agent_kind is None
    assert not comment.is_agent


def test_a_marker_in_the_middle_of_a_comment_does_not_count():
    body = "quoting <!-- issue-agent:clarification --> and then more text"
    assert parse_comment("ann", body).agent_kind is None


def test_label_on_an_untouched_issue_goes_ahead():
    assert skip_reason("label", []) is None
    assert skip_reason("label", [HUMAN]) is None


def test_label_is_skipped_when_the_agent_had_the_last_word():
    for kind in ("clarification", "no-change", "declined", "escalated"):
        assert skip_reason("label", [HUMAN, agent(kind)]) is not None, kind


def test_label_goes_ahead_after_the_agent_had_the_last_word_but_a_person_replied():
    assert skip_reason("label", [agent("clarification"), HUMAN]) is None


def test_label_may_retry_after_the_agent_crashed():
    assert skip_reason("label", [agent(ERROR)]) is None


def test_reply_resumes_only_after_a_clarification_question():
    assert skip_reason("reply", [agent("clarification"), HUMAN]) is None
    assert skip_reason("reply", [HUMAN, agent("clarification"), HUMAN, HUMAN]) is None


def test_reply_is_ignored_when_the_agent_was_not_asking_anything():
    assert skip_reason("reply", [HUMAN]) is not None
    assert skip_reason("reply", [agent("no-change"), HUMAN]) is not None
    assert skip_reason("reply", [agent("declined"), HUMAN]) is not None


def test_reply_is_ignored_when_no_answer_has_actually_arrived():
    assert skip_reason("reply", [agent("clarification")]) is not None


def test_reply_to_an_old_question_is_ignored_once_the_agent_moved_on():
    thread = [agent("clarification"), HUMAN, agent("no-change"), HUMAN]
    assert skip_reason("reply", thread) is not None
