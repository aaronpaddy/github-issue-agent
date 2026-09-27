"""A cheap gate that runs before any cloning or coding: is this issue specific enough to act on?

Whether to start is a different question from how to fix, and it is a bad fit for the main
agent prompt, where a long tool-using context biases the model toward doing something. Asked
on its own, about the issue text alone, the model judges it much more cleanly, and a vague
issue costs a fraction of a cent to turn away instead of a full agent run.

The gate fails open: if the call yields no usable verdict, work proceeds, because a triage
hiccup must never block a legitimate issue.
"""

from __future__ import annotations

from typing import Any

from src.agent.llm import LLMClient
from src.agent.state import Clarification, Job

TRIAGE_TOOL = "triage"

SYSTEM_PROMPT = """\
You review GitHub issues for an autonomous coding agent before it starts work. Decide whether \
the issue's GOAL is defined well enough to act on without inventing requirements. Judge the \
issue text alone; you cannot see the code.

An issue is ACTIONABLE when the goal is clear. That includes: a concrete bug (what happens \
versus what should happen), specific behavior to add, tests for named code, and a question \
the agent can settle by reading the code, including "is there X? If not, please add it" (the \
agent will check whether it exists). Missing details are NOT grounds for clarification. The \
agent chooses sensible defaults for naming, formats, and structure and states its assumptions, \
and an example in the issue settles the format.

An issue NEEDS CLARIFICATION only when the goal itself is undefined: it names no concrete \
behavior, and several materially different changes would each plausibly satisfy it, so any \
choice would be the agent inventing product requirements. Examples: "make it smarter", \
"improve performance", "support other currencies" with no idea what supporting means.

When in doubt, choose actionable: the agent can still ask once it has looked at the code. \
Call the triage tool with your verdict."""

TRIAGE_SCHEMA: dict[str, Any] = {
    "name": TRIAGE_TOOL,
    "description": "Report whether the issue is specific enough to act on.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["actionable", "needs_clarification"]},
            "reason": {
                "type": "string",
                "description": "For needs_clarification: what is unclear and the different "
                "things it could mean.",
            },
            "question": {
                "type": "string",
                "description": "For needs_clarification: one specific question that, once "
                "answered, would make the issue actionable.",
            },
        },
        "required": ["verdict"],
    },
}


def triage_issue(llm: LLMClient, job: Job) -> Clarification | None:
    """Returns the question to post if the issue is too vague to start on, else None."""
    response = llm.call(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": job.issue_context()}],
        tools=[TRIAGE_SCHEMA],
    )
    for call in response.tool_calls:
        if call.name != TRIAGE_TOOL or call.input.get("verdict") != "needs_clarification":
            continue
        question = str(call.input.get("question", "")).strip()
        if question:
            return Clarification(question=question, findings=str(call.input.get("reason", "")).strip())
    return None
