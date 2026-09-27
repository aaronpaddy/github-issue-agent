"""Job and AgentStatus: the state machine that enforces the retry cap and
escalation path. The loop in core.py mutates a Job's state field explicitly
at each transition — there's no implicit "keep going until the model says
stop."
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime


class AgentStatus(str, enum.Enum):
    QUEUED = "queued"
    INVESTIGATING = "investigating"
    IMPLEMENTING = "implementing"
    VALIDATING = "validating"
    OPENING_PR = "opening_pr"
    DONE = "done"
    ESCALATED = "escalated"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_CHANGE_NEEDED = "no_change_needed"


@dataclass
class ValidationOutcome:
    passed: bool
    checks: dict[str, bool]  # e.g. {"pytest": True, "ruff": False, ...}
    detail: str


@dataclass
class Assessment:
    """The agent's own account of what it did and how sure it is."""

    summary: str
    confidence: str  # "high" | "medium" | "low"
    interpretation: str
    assumptions: list[str] = field(default_factory=list)


@dataclass
class Clarification:
    """A question the agent needs answered before it can act."""

    question: str
    findings: str


@dataclass
class NoChangeNeeded:
    """The agent's finding that the issue needs no code change (already fixed, intended, ...)."""

    reason: str
    evidence: str


@dataclass
class Attempt:
    """One implement -> validate cycle within a job."""

    number: int
    validation: ValidationOutcome | None = None
    summary: str = ""


@dataclass
class Job:
    id: str
    repo: str
    issue_number: int
    issue_title: str
    issue_body: str
    issue_comments: list[str] = field(default_factory=list)

    status: AgentStatus = AgentStatus.QUEUED
    max_attempts: int = 3
    attempts: list[Attempt] = field(default_factory=list)

    branch_name: str | None = None
    pr_url: str | None = None
    escalation_reason: str | None = None
    assessment: Assessment | None = None
    clarification: Clarification | None = None
    no_change: NoChangeNeeded | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def current_attempt_number(self) -> int:
        return len(self.attempts) + 1

    def attempts_exhausted(self) -> bool:
        return len(self.attempts) >= self.max_attempts

    def finish(self, status: AgentStatus) -> None:
        self.status = status
        self.finished_at = datetime.now(UTC)

    def issue_context(self) -> str:
        parts = [f"Issue #{self.issue_number}: {self.issue_title}", "", self.issue_body]
        if self.issue_comments:
            parts.append("\nComments:")
            parts.extend(f"- {c}" for c in self.issue_comments)
        return "\n".join(parts)
