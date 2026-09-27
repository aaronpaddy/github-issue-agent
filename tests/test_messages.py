from src.agent.policy import Action, Decision
from src.agent.state import Assessment, Clarification, NoChangeNeeded
from src.github.client import Overlap
from src.github.messages import (
    build_pr_body,
    clarification_comment,
    declined_comment,
    escalation_comment,
    no_change_comment,
)


def test_pr_body_adds_a_summary_heading_when_the_model_did_not():
    body = build_pr_body(7, "Fixed the rounding.", 1)
    assert body.startswith("Fixes #7")
    assert body.count("## Summary") == 1


def test_pr_body_does_not_stack_a_second_heading():
    body = build_pr_body(7, "## Summary\n\nFixed the rounding.", 2)
    assert body.count("## Summary") == 1
    assert "after 2 attempt(s)" in body


def test_pr_body_drops_chatter_before_the_models_own_heading():
    body = build_pr_body(4, "All checks pass now.\n\n## Summary\n\n- Added the helper.", 1)
    assert body.count("## Summary") == 1
    assert "All checks pass now" not in body
    assert "Added the helper" in body


ASSESSMENT = Assessment(
    summary="Reworked rounding.",
    confidence="medium",
    interpretation="Splits should never lose cents.",
    assumptions=["Only USD matters"],
)


def test_a_clean_pr_closes_the_issue_and_lists_the_assessment():
    decision = Decision(Action.OPEN_PR, closes_issue=True)
    body = build_pr_body(3, "Reworked rounding.", 1, ASSESSMENT, decision)
    assert body.startswith("Fixes #3")
    assert "## Reviewer notes" in body
    assert "**Confidence:** medium" in body
    assert "Splits should never lose cents." in body
    assert "Only USD matters" in body
    assert "Opened as a draft" not in body


def test_a_draft_pr_only_references_the_issue_and_explains_why():
    decision = Decision(Action.OPEN_DRAFT_PR, closes_issue=False, reasons=["No tests were added."])
    body = build_pr_body(3, "Reworked rounding.", 2, ASSESSMENT, decision)
    assert body.startswith("Refs #3")
    assert "Opened as a draft because" in body
    assert "No tests were added." in body


def test_overlapping_prs_are_called_out():
    overlaps = [Overlap(number=8, title="Add tests", files=["tests/test_settle.py"])]
    body = build_pr_body(4, "Added helper.", 1, ASSESSMENT, overlaps=overlaps)
    assert "**Overlaps with #8**" in body
    assert "`tests/test_settle.py`" in body
    assert "merge conflict" in body


def test_no_reviewer_notes_section_without_anything_to_say():
    assert "## Reviewer notes" not in build_pr_body(3, "Did it.", 1)


def test_clarification_comment_has_findings_and_a_question():
    text = clarification_comment(Clarification(question="Which currencies?", findings="None named."))
    assert "None named." in text
    assert "Which currencies?" in text


def test_declined_comment_explains_the_interpretation_and_why_it_held_back():
    decision = Decision(Action.COMMENT_ONLY, closes_issue=False, reasons=["It was guessing."])
    text = declined_comment(ASSESSMENT, decision)
    assert "haven't opened a pull request" in text
    assert "Splits should never lose cents." in text
    assert "Only USD matters" in text
    assert "It was guessing." in text


def test_escalation_comment_includes_the_reason():
    assert "budget reached" in escalation_comment("budget reached")


def test_no_change_comment_explains_and_does_not_claim_to_close():
    text = no_change_comment(NoChangeNeeded(reason="Already fixed.", evidence="See PR #9."))
    assert "Already fixed." in text
    assert "See PR #9." in text
    assert "haven't closed the issue" in text
