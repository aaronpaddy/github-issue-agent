"""Eval cases: an issue, the commit it applies to, and how to judge what the agent does."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# What the agent is expected to do with a case:
#   fixed      - produce a validated change (then hidden tests and mutants judge its quality)
#   asks       - decline to guess and ask for clarification
#   no_change  - conclude nothing needs changing
EXPECTATIONS = ("fixed", "asks", "no_change")

CASES_DIR = Path(__file__).resolve().parents[2] / "evals" / "cases"


@dataclass(frozen=True)
class Mutant:
    """A deliberate bug. A good test suite should fail when it is applied."""

    file: str
    old: str
    new: str
    count: int = 1  # how many occurrences to replace (the first N)


@dataclass(frozen=True)
class CaseComment:
    author: str
    body: str
    agent_kind: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    description: str
    repo: str
    base_sha: str
    issue_number: int
    issue_title: str
    issue_body: str
    expect: str
    comments: tuple[CaseComment, ...] = ()
    trigger: str = "label"
    hidden_test: str | None = None
    mutants: tuple[Mutant, ...] = ()
    min_mutants_caught: int = 0
    require_tests_added: bool = False
    # Strings that must not appear in the agent's change (e.g. text an injected instruction
    # tried to get it to write). Checked against the final diff.
    forbidden_in_diff: tuple[str, ...] = ()
    directory: Path = field(default=Path("."), compare=False)

    @property
    def hidden_test_path(self) -> Path | None:
        return self.directory / self.hidden_test if self.hidden_test else None


def load_case(directory: Path) -> Case:
    data: dict[str, Any] = json.loads((directory / "case.json").read_text())
    if data["expect"] not in EXPECTATIONS:
        raise ValueError(f"{directory.name}: expect must be one of {EXPECTATIONS}")
    issue = data["issue"]
    case = Case(
        id=data["id"],
        description=data["description"],
        repo=data["repo"],
        base_sha=data["base_sha"],
        issue_number=issue["number"],
        issue_title=issue["title"],
        issue_body=issue["body"],
        expect=data["expect"],
        comments=tuple(CaseComment(**c) for c in issue.get("comments", [])),
        trigger=data.get("trigger", "label"),
        hidden_test=data.get("hidden_test"),
        mutants=tuple(Mutant(**m) for m in data.get("mutants", [])),
        min_mutants_caught=data.get("min_mutants_caught", 0),
        require_tests_added=data.get("require_tests_added", False),
        forbidden_in_diff=tuple(data.get("forbidden_in_diff", ())),
        directory=directory,
    )
    if case.id != directory.name:
        raise ValueError(f"{directory.name}: id '{case.id}' must match the directory name")
    if case.hidden_test_path and not case.hidden_test_path.is_file():
        raise ValueError(f"{case.id}: missing hidden test {case.hidden_test}")
    return case


def load_cases(cases_dir: Path = CASES_DIR) -> list[Case]:
    return [load_case(d) for d in sorted(cases_dir.iterdir()) if (d / "case.json").is_file()]
