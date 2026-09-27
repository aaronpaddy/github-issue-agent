"""Decides how a validated fix is presented to the maintainers.

A passing validation says the change is *safe*, not that it is *right*. The
model's own confidence is useful but poorly calibrated, so it is never the
only input: it is combined with objective facts about the work (retries
needed, whether tests were added, how large the diff is), and the most
conservative signal wins.

  high confidence and clean signals  -> normal PR that closes the issue
  any doubt                          -> draft PR that only references the issue
  low confidence / no assessment     -> no PR; explain on the issue instead

This is a pure function so every rule is covered by fast unit tests.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from src.agent.state import Assessment


class Action(str, enum.Enum):
    OPEN_PR = "open_pr"
    OPEN_DRAFT_PR = "open_draft_pr"
    COMMENT_ONLY = "comment_only"


@dataclass(frozen=True)
class DiffStats:
    files: tuple[str, ...] = ()
    lines_added: int = 0
    lines_deleted: int = 0

    @property
    def changed_lines(self) -> int:
        return self.lines_added + self.lines_deleted


@dataclass(frozen=True)
class Limits:
    max_files: int = 8
    max_changed_lines: int = 300


@dataclass
class Decision:
    action: Action
    closes_issue: bool
    reasons: list[str] = field(default_factory=list)


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name
    return (
        "tests" in p.parts
        or "test" in p.parts
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js"))
    )


def _is_source_code(path: str) -> bool:
    return path.endswith((".py", ".ts", ".js", ".go", ".java", ".rb")) and not is_test_path(path)


def decide(
    assessment: Assessment | None,
    attempts: int,
    stats: DiffStats,
    limits: Limits | None = None,
) -> Decision:
    limits = limits or Limits()
    if assessment is None:
        return Decision(
            Action.COMMENT_ONLY,
            closes_issue=False,
            reasons=["The agent did not report a self-assessment, so its confidence is unknown."],
        )
    if assessment.confidence == "low":
        return Decision(
            Action.COMMENT_ONLY,
            closes_issue=False,
            reasons=["The agent reported low confidence, meaning it was guessing."],
        )

    doubts: list[str] = []
    if assessment.confidence == "medium":
        doubts.append(
            "The agent reported medium confidence: it interpreted a loosely worded issue "
            "or made a judgment call."
        )
    if attempts > 1:
        doubts.append(f"The fix needed {attempts} attempts to pass validation.")

    changed_source = [f for f in stats.files if _is_source_code(f)]
    changed_tests = [f for f in stats.files if is_test_path(f)]
    if changed_source and not changed_tests:
        doubts.append("Source code changed but no tests were added or updated.")

    if len(stats.files) > limits.max_files:
        doubts.append(f"The change touches {len(stats.files)} files (limit {limits.max_files}).")
    if stats.changed_lines > limits.max_changed_lines:
        doubts.append(
            f"The change is {stats.changed_lines} lines (limit {limits.max_changed_lines})."
        )

    if doubts:
        return Decision(Action.OPEN_DRAFT_PR, closes_issue=False, reasons=doubts)
    return Decision(Action.OPEN_PR, closes_issue=True)
