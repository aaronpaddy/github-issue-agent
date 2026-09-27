import hashlib
import hmac
import json

import pytest
from fakeredis import FakeRedis
from fastapi.testclient import TestClient
from rq import Queue

from src.config import Settings
from src.service.app import create_app
from src.service.queue import JOB_FUNCTION, lock_key, release_lock

SECRET = "s3cret"


def make_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "github_token": "t",
        "github_repo": "aaronpaddy/expense-splitter",
        "webhook_secret": SECRET,
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
def queue():
    return Queue("test", connection=FakeRedis())


@pytest.fixture
def client(queue):
    return TestClient(create_app(make_settings(), queue=queue))


def post(client, payload, event="issues", secret=SECRET, signed=True):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if signed:
        headers["X-Hub-Signature-256"] = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
    return client.post("/webhooks/github", content=body, headers=headers)


LABELED = {
    "action": "labeled",
    "label": {"name": "agent"},
    "issue": {"number": 7, "state": "open"},
    "repository": {"full_name": "aaronpaddy/expense-splitter"},
    "sender": {"login": "aaronpaddy", "type": "User"},
}


def test_refuses_to_start_without_a_webhook_secret():
    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        create_app(make_settings(webhook_secret=None), queue=Queue("t", connection=FakeRedis()))


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_unsigned_and_badly_signed_requests_are_rejected(client, queue):
    assert post(client, LABELED, signed=False).status_code == 401
    assert post(client, LABELED, secret="wrong").status_code == 401
    assert len(queue) == 0


def test_ping_gets_a_pong(client):
    response = post(client, {"zen": "hi"}, event="ping")
    assert response.status_code == 200
    assert response.json() == {"status": "pong"}


def test_the_trigger_label_queues_exactly_one_job(client, queue):
    response = post(client, LABELED)
    assert response.status_code == 202
    assert response.json() == {"status": "queued"}
    assert len(queue) == 1
    job = queue.jobs[0]
    assert job.func_name == JOB_FUNCTION
    assert job.args == ("aaronpaddy/expense-splitter", 7, "label")


def test_a_redelivered_webhook_is_a_duplicate_not_a_second_run(client, queue):
    assert post(client, LABELED).status_code == 202
    again = post(client, LABELED)
    assert again.status_code == 200
    assert again.json() == {"status": "duplicate"}
    assert len(queue) == 1


def test_the_issue_can_run_again_after_the_lock_is_released(client, queue):
    post(client, LABELED)
    release_lock(queue.connection, "aaronpaddy/expense-splitter", 7)
    assert post(client, LABELED).status_code == 202
    assert len(queue) == 2


def test_the_lock_key_ignores_repo_case():
    assert lock_key("Aaron/Repo", 3) == lock_key("aaron/repo", 3)


def test_irrelevant_events_are_ignored(client, queue):
    other = {**LABELED, "label": {"name": "bug"}}
    assert post(client, other).json() == {"status": "ignored"}
    assert post(client, LABELED, event="push").json() == {"status": "ignored"}
    assert len(queue) == 0


def test_repos_outside_the_allowlist_are_ignored(queue):
    client = TestClient(create_app(make_settings(allowed_repos="someone/else"), queue=queue))
    assert post(client, LABELED).json() == {"status": "ignored"}
    assert len(queue) == 0


def test_invalid_json_with_a_valid_signature_is_a_400(client):
    body = b"not json"
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": signature},
    )
    assert response.status_code == 400


REPLY = {
    "action": "created",
    "comment": {"user": {"login": "aaronpaddy", "type": "User"}, "body": "USD and EUR"},
    "issue": {"number": 7, "state": "open", "labels": [{"name": "agent"}]},
    "repository": {"full_name": "aaronpaddy/expense-splitter"},
    "sender": {"login": "aaronpaddy", "type": "User"},
}


def test_a_reply_on_an_opted_in_issue_queues_a_reply_run(client, queue):
    response = post(client, REPLY, event="issue_comment")
    assert response.status_code == 202
    assert queue.jobs[0].args == ("aaronpaddy/expense-splitter", 7, "reply")


def test_a_label_run_and_a_reply_run_share_one_lock(client, queue):
    assert post(client, LABELED).status_code == 202
    assert post(client, REPLY, event="issue_comment").json() == {"status": "duplicate"}
    assert len(queue) == 1


def test_a_comment_on_an_issue_without_the_label_is_ignored(client, queue):
    payload = {**REPLY, "issue": {"number": 7, "state": "open", "labels": [{"name": "bug"}]}}
    assert post(client, payload, event="issue_comment").json() == {"status": "ignored"}
    assert len(queue) == 0
