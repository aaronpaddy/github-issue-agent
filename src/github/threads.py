"""Reading an issue's conversation: who said what, and whether the agent should act again.

Every comment the agent posts ends with a hidden marker that records its kind. That lets the
agent recognise its own earlier messages regardless of which identity it posted as, and lets
it tell "waiting for an answer" apart from "already said what it had to say".
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.agent.state import IssueComment

_MARKER = re.compile(r"\s*<!-- issue-agent:([a-z-]+) -->\s*$")

CLARIFICATION = "clarification"
NO_CHANGE = "no-change"
DECLINED = "declined"
ESCALATED = "escalated"
ERROR = "error"
GAVE_UP = "gave-up"


def tag(body: str, kind: str) -> str:
    """Append the hidden marker that identifies a comment as the agent's."""
    return f"{body.rstrip()}\n\n<!-- issue-agent:{kind} -->"


def parse_comment(author: str, body: str, comment_id: int | None = None) -> IssueComment:
    """Build an IssueComment, stripping the marker and recording the kind if there is one."""
    match = _MARKER.search(body)
    if not match:
        return IssueComment(author=author, body=body.strip(), id=comment_id)
    return IssueComment(
        author=author,
        body=body[: match.start()].strip(),
        agent_kind=match.group(1),
        id=comment_id,
    )


def clarification_rounds(comments: Sequence[IssueComment]) -> int:
    """How many times the agent has already asked for clarification on this issue."""
    return sum(1 for c in comments if c.agent_kind == CLARIFICATION)


def skip_reason(trigger: str, comments: Sequence[IssueComment]) -> str | None:
    """Why a run should not happen, judged from the conversation alone (None means go ahead).

    A `label` trigger is skipped when the agent already had the last word, because re-running
    on unchanged input would just repeat it. A `reply` trigger is skipped unless the agent is
    actually waiting for an answer to a clarification question.
    """
    last = comments[-1] if comments else None
    last_agent = next((c for c in reversed(comments) if c.is_agent), None)

    if trigger == "reply":
        if last_agent is None or last_agent.agent_kind != CLARIFICATION:
            return "the agent isn't waiting for a reply on this issue"
        if last is not None and last.is_agent:
            return "no reply has arrived since the agent's question"
        return None

    if last is not None and last.is_agent and last.agent_kind != ERROR:
        return "the agent already responded here; reply on the issue to continue"
    return None
