"""The text the agent posts on GitHub: PR descriptions and issue comments."""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.agent.policy import Action, Decision
from src.agent.state import Assessment, Clarification, NoChangeNeeded
from src.github.client import Overlap

_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)


def build_pr_body(
    issue_number: int,
    summary: str,
    attempts: int,
    assessment: Assessment | None = None,
    decision: Decision | None = None,
    overlaps: Sequence[Overlap] = (),
) -> str:
    keyword = "Fixes" if decision is None or decision.closes_issue else "Refs"
    summary = summary.strip()
    # The model often writes its own headings, sometimes after a line of chatter. If it did,
    # keep everything from its first heading; otherwise supply one.
    heading = _HEADING.search(summary)
    summary = summary[heading.start() :] if heading else f"## Summary\n{summary}"

    sections = [f"{keyword} #{issue_number}", summary]

    notes = _reviewer_notes(assessment, decision, overlaps)
    if notes:
        sections.append(notes)

    sections.append(
        "## Validation\n"
        "No new issues from pytest, ruff, black, or mypy compared with the base branch, "
        f"after {attempts} attempt(s)."
    )
    return "\n\n".join(sections)


def _reviewer_notes(
    assessment: Assessment | None, decision: Decision | None, overlaps: Sequence[Overlap]
) -> str:
    lines: list[str] = []
    if assessment:
        lines.append(f"- **Confidence:** {assessment.confidence} (self-reported)")
        if assessment.interpretation:
            lines.append(f"- **How the issue was interpreted:** {assessment.interpretation}")
        if assessment.assumptions:
            lines.append("- **Assumptions:**")
            lines.extend(f"  - {a}" for a in assessment.assumptions)
    if decision and decision.action == Action.OPEN_DRAFT_PR:
        lines.append("- **Opened as a draft because:**")
        lines.extend(f"  - {reason}" for reason in decision.reasons)
    for overlap in overlaps:
        files = ", ".join(f"`{f}`" for f in overlap.files)
        lines.append(
            f"- **Overlaps with #{overlap.number}** ({overlap.title}) on {files}; "
            "expect a merge conflict in whichever lands second."
        )
    return "## Reviewer notes\n" + "\n".join(lines) if lines else ""


def clarification_comment(clarification: Clarification) -> str:
    return (
        "I looked into this but need more information before I can make a change.\n\n"
        f"**What I found**\n{clarification.findings}\n\n"
        f"**Question**\n{clarification.question}"
    )


def no_change_comment(finding: NoChangeNeeded) -> str:
    return (
        "I investigated this and don't think a code change is needed.\n\n"
        f"**Why**\n{finding.reason}\n\n"
        f"**What I checked**\n{finding.evidence}\n\n"
        "I haven't closed the issue. If I've misunderstood what's still wrong, let me know "
        "and I'll take another look."
    )


def declined_comment(assessment: Assessment | None, decision: Decision) -> str:
    intro = (
        "I investigated this and produced a change that passes the checks, but I'm not "
        "confident it is what you want, so I haven't opened a pull request."
    )
    parts = [intro]
    if assessment and assessment.interpretation:
        parts.append(f"**How I interpreted the issue**\n{assessment.interpretation}")
    if assessment and assessment.assumptions:
        parts.append("**Assumptions I would have had to make**\n" + "\n".join(f"- {a}" for a in assessment.assumptions))
    parts.append("**Why I held back**\n" + "\n".join(f"- {r}" for r in decision.reasons))
    parts.append("If you can clarify what you're after, I can try again.")
    return "\n\n".join(parts)


def escalation_comment(reason: str) -> str:
    return f"I attempted this issue but couldn't reach a validated fix.\n\n{reason}"
