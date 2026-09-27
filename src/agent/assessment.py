"""The ways the model ends its work, exposed as tools it must call.

All three are handled by the agent loop itself rather than the tool registry,
because they change control flow instead of touching the repository:

  submit_result          "I'm done": a summary plus an honest self-assessment.
  request_clarification  "I can't act without inventing requirements": stop and ask.
  report_no_change_needed "It's already fixed / not reproducible / intended": stop and say so.

Asking for structure (instead of parsing free text) means the confidence and
assumptions arrive as data the application can apply policy to.
"""

from __future__ import annotations

from typing import Any

from src.agent.state import Assessment, Clarification, NoChangeNeeded

SUBMIT_RESULT = "submit_result"
REQUEST_CLARIFICATION = "request_clarification"
REPORT_NO_CHANGE = "report_no_change_needed"

CONFIDENCE_LEVELS = ("high", "medium", "low")

SUBMIT_RESULT_SCHEMA: dict[str, Any] = {
    "name": SUBMIT_RESULT,
    "description": (
        "Call this when your change is complete, instead of writing a final message. The "
        "application will then run the full validation suite. Be honest in the assessment: "
        "it decides how your work is presented to the maintainers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "What was wrong and what you changed, for a pull request description.",
            },
            "confidence": {
                "type": "string",
                "enum": list(CONFIDENCE_LEVELS),
                "description": (
                    "high: the issue was specific and you addressed exactly what it asked. "
                    "medium: you had to interpret a loosely worded issue or make a judgment call. "
                    "low: you are guessing."
                ),
            },
            "interpretation": {
                "type": "string",
                "description": "In one or two sentences, what you understood the issue to be asking for.",
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Anything you assumed that the issue did not state. Empty if none.",
            },
        },
        "required": ["summary", "confidence", "interpretation"],
    },
}

REQUEST_CLARIFICATION_SCHEMA: dict[str, Any] = {
    "name": REQUEST_CLARIFICATION,
    "description": (
        "Call this instead of making changes when the issue is too vague to act on without "
        "inventing requirements (for example it names no concrete behavior, or several very "
        "different changes would each satisfy it). Do not guess. The run stops and your "
        "question is posted on the issue."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "A specific question that, once answered, would let you proceed.",
            },
            "findings": {
                "type": "string",
                "description": "What you looked at and why the issue is ambiguous.",
            },
        },
        "required": ["question", "findings"],
    },
}

REPORT_NO_CHANGE_SCHEMA: dict[str, Any] = {
    "name": REPORT_NO_CHANGE,
    "description": (
        "Call this instead of making changes when, after investigating, the issue needs no "
        "code change: it is already fixed (for example by a recent commit), the behavior it "
        "describes cannot be reproduced, or it is working as designed. Do not invent a change "
        "to justify the run. The run stops and your finding is posted on the issue; the "
        "maintainers decide whether to close it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Why no change is needed."},
            "evidence": {
                "type": "string",
                "description": "What you checked: commits, files, test results, reproduction attempts.",
            },
        },
        "required": ["reason", "evidence"],
    },
}

TERMINAL_TOOL_SCHEMAS = [SUBMIT_RESULT_SCHEMA, REQUEST_CLARIFICATION_SCHEMA, REPORT_NO_CHANGE_SCHEMA]


def parse_assessment(tool_input: dict[str, Any]) -> Assessment:
    confidence = str(tool_input.get("confidence", "")).lower()
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "low"  # an unreadable self-assessment is treated as no confidence
    assumptions = tool_input.get("assumptions") or []
    return Assessment(
        summary=str(tool_input.get("summary", "")).strip(),
        confidence=confidence,
        interpretation=str(tool_input.get("interpretation", "")).strip(),
        assumptions=[str(a).strip() for a in assumptions if str(a).strip()],
    )


def parse_no_change(tool_input: dict[str, Any]) -> NoChangeNeeded:
    return NoChangeNeeded(
        reason=str(tool_input.get("reason", "")).strip(),
        evidence=str(tool_input.get("evidence", "")).strip(),
    )


def parse_clarification(tool_input: dict[str, Any]) -> Clarification:
    return Clarification(
        question=str(tool_input.get("question", "")).strip(),
        findings=str(tool_input.get("findings", "")).strip(),
    )
