"""Pure webhook logic: is this request really from GitHub, and does it ask for work?

Kept free of FastAPI and Redis so every rule is a fast unit test.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Trigger:
    repo: str
    issue_number: int
    sender: str
    kind: str = "label"  # "label": the trigger label was applied; "reply": someone commented


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Check GitHub's `X-Hub-Signature-256` header against the raw request body."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def parse_trigger(
    event: str,
    payload: dict[str, Any],
    *,
    trigger_label: str,
    allowed_repos: Collection[str],
) -> Trigger | None:
    """A Trigger if a person either applied the trigger label to an open issue, or replied on
    an open issue that already carries it, in an allowed repo.

    A reply only says "wake up and look": whether the agent was actually waiting for one is
    checked later, against the conversation, before anything costs money.
    """
    action = payload.get("action")
    if event == "issues" and action == "labeled":
        kind = "label"
        if ((payload.get("label") or {}).get("name") or "").lower() != trigger_label.lower():
            return None
    elif event == "issue_comment" and action == "created":
        kind = "reply"
        labels = {(lbl.get("name") or "").lower() for lbl in (payload.get("issue") or {}).get("labels") or []}
        if trigger_label.lower() not in labels:
            return None
        if ((payload.get("comment") or {}).get("user") or {}).get("type") == "Bot":
            return None
    else:
        return None

    repo = (payload.get("repository") or {}).get("full_name", "")
    if repo.lower() not in {r.lower() for r in allowed_repos}:
        return None

    sender = payload.get("sender") or {}
    if sender.get("type") == "Bot":  # never react to automation, including ourselves
        return None

    issue = payload.get("issue") or {}
    if "pull_request" in issue or issue.get("state") != "open":
        return None

    number = issue.get("number")
    if not isinstance(number, int):
        return None
    return Trigger(repo=repo, issue_number=number, sender=sender.get("login", ""), kind=kind)
