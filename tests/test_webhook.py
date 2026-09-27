import hashlib
import hmac

from src.service.webhook import Trigger, parse_trigger, verify_signature

SECRET = "s3cret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correct_signature_verifies():
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, sign(body))


def test_a_wrong_secret_or_tampered_body_fails():
    body = b'{"a": 1}'
    assert not verify_signature(SECRET, body, sign(body, secret="other"))
    assert not verify_signature(SECRET, b'{"a": 2}', sign(body))


def test_missing_or_malformed_signatures_fail():
    assert not verify_signature(SECRET, b"x", None)
    assert not verify_signature(SECRET, b"x", "")
    assert not verify_signature(SECRET, b"x", "md5=abc")


def labeled_payload(**overrides):
    payload = {
        "action": "labeled",
        "label": {"name": "agent"},
        "issue": {"number": 7, "state": "open"},
        "repository": {"full_name": "aaronpaddy/expense-splitter"},
        "sender": {"login": "aaronpaddy", "type": "User"},
    }
    payload.update(overrides)
    return payload


ALLOWED = ["aaronpaddy/expense-splitter"]


def trigger(payload, event="issues", label="agent", allowed=ALLOWED):
    return parse_trigger(event, payload, trigger_label=label, allowed_repos=allowed)


def test_labeling_an_open_issue_with_the_trigger_label_triggers_a_run():
    assert trigger(labeled_payload()) == Trigger("aaronpaddy/expense-splitter", 7, "aaronpaddy")


def test_the_label_and_repo_match_ignore_case():
    payload = labeled_payload(label={"name": "AGENT"})
    payload["repository"]["full_name"] = "AaronPaddy/Expense-Splitter"
    assert trigger(payload) is not None


def test_other_events_and_actions_are_ignored():
    assert trigger(labeled_payload(), event="issue_comment") is None
    assert trigger(labeled_payload(action="opened")) is None
    assert trigger(labeled_payload(action="unlabeled")) is None


def test_other_labels_are_ignored():
    assert trigger(labeled_payload(label={"name": "bug"})) is None


def test_repos_outside_the_allowlist_are_ignored():
    assert trigger(labeled_payload(), allowed=["someone/else"]) is None


def test_bot_senders_are_ignored():
    assert trigger(labeled_payload(sender={"login": "x[bot]", "type": "Bot"})) is None


def test_pull_requests_and_closed_issues_are_ignored():
    assert trigger(labeled_payload(issue={"number": 7, "state": "open", "pull_request": {}})) is None
    assert trigger(labeled_payload(issue={"number": 7, "state": "closed"})) is None


def test_a_payload_without_an_issue_number_is_ignored():
    assert trigger(labeled_payload(issue={"state": "open"})) is None


def reply_payload(**overrides):
    payload = {
        "action": "created",
        "comment": {"user": {"login": "aaronpaddy", "type": "User"}},
        "issue": {"number": 7, "state": "open", "labels": [{"name": "agent"}]},
        "repository": {"full_name": "aaronpaddy/expense-splitter"},
        "sender": {"login": "aaronpaddy", "type": "User"},
    }
    payload.update(overrides)
    return payload


def test_a_person_replying_on_a_labeled_issue_triggers_a_reply_run():
    result = trigger(reply_payload(), event="issue_comment")
    assert result == Trigger("aaronpaddy/expense-splitter", 7, "aaronpaddy", kind="reply")


def test_a_label_event_produces_a_label_run():
    assert trigger(labeled_payload()).kind == "label"


def test_replies_on_issues_without_the_trigger_label_are_ignored():
    issue = {"number": 7, "state": "open", "labels": [{"name": "bug"}]}
    assert trigger(reply_payload(issue=issue), event="issue_comment") is None


def test_replies_from_bots_and_on_pull_requests_or_closed_issues_are_ignored():
    bot = {"user": {"login": "x[bot]", "type": "Bot"}}
    assert trigger(reply_payload(comment=bot), event="issue_comment") is None
    assert trigger(reply_payload(sender={"login": "x", "type": "Bot"}), event="issue_comment") is None
    pr = {"number": 7, "state": "open", "labels": [{"name": "agent"}], "pull_request": {}}
    assert trigger(reply_payload(issue=pr), event="issue_comment") is None
    closed = {"number": 7, "state": "closed", "labels": [{"name": "agent"}]}
    assert trigger(reply_payload(issue=closed), event="issue_comment") is None


def test_only_new_comments_trigger_not_edits_or_deletions():
    assert trigger(reply_payload(action="edited"), event="issue_comment") is None
    assert trigger(reply_payload(action="deleted"), event="issue_comment") is None
