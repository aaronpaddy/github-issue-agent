"""Validator: runs the repo's real validation commands and produces a
structured ValidationOutcome. This is what turns "the model thinks it's
done" into "the change is objectively verified".

Real repositories rarely start clean (a fresh clone of the target project
has hundreds of pre-existing lint and type errors), so the gate is relative
to a baseline captured before the agent touches anything:

  - a check that passed at baseline must still pass;
  - a check that already failed at baseline may keep failing, but the agent
    must not introduce any *new* issue.

Issues are compared as normalized lines (line numbers stripped), so code that
merely shifts down a few lines is not reported as a regression.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from src.agent.state import ValidationOutcome
from src.agent.workspace import Workspace

CHECK_TIMEOUT_SECONDS = 300
MAX_ISSUES_SHOWN = 40
OUTPUT_TAIL_CHARS = 3000

_LINE_NUMBERS = re.compile(r"(:\d+){1,2}(?=:)")


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]
    # Matches the lines of tool output that each represent one issue.
    issue_pattern: re.Pattern[str]
    # Drop everything from this marker onward when normalizing (e.g. volatile messages).
    truncate_at: str | None = None
    # Also show the raw output tail on failure (tracebacks, for test runs).
    include_output_tail: bool = False


# Cheapest checks first so a quick lint failure doesn't wait behind a test run.
DEFAULT_CHECKS: tuple[Check, ...] = (
    Check("ruff", ("ruff", "check", ".", "--output-format=concise"), re.compile(r"^\S+?:\d+:\d+: ")),
    Check("black", ("black", "--check", "."), re.compile(r"^would reformat ")),
    Check("mypy", ("mypy", "src"), re.compile(r"^\S+?:\d+: (error|warning):")),
    Check(
        "pytest",
        ("pytest", "-q", "-rf"),
        re.compile(r"^FAILED "),
        truncate_at=" - ",
        include_output_tail=True,
    ),
)


@dataclass
class CheckResult:
    returncode: int
    output: str
    issues: Counter[str] = field(default_factory=Counter)
    originals: dict[str, list[str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def _normalize(check: Check, line: str) -> str:
    if check.truncate_at and check.truncate_at in line:
        line = line.split(check.truncate_at, 1)[0]
    return _LINE_NUMBERS.sub("", line).strip()


def run_check(workspace: Workspace, check: Check) -> CheckResult:
    proc = workspace.run(list(check.argv), timeout=CHECK_TIMEOUT_SECONDS)
    output = (proc.stdout + "\n" + proc.stderr).strip()
    result = CheckResult(returncode=proc.returncode, output=output)
    if proc.ok:
        return result
    for line in output.splitlines():
        if check.issue_pattern.match(line):
            key = _normalize(check, line)
            result.issues[key] += 1
            result.originals.setdefault(key, []).append(line)
    return result


class Validator:
    def __init__(
        self,
        checks: tuple[Check, ...] = DEFAULT_CHECKS,
        baseline: dict[str, CheckResult] | None = None,
    ):
        self.checks = checks
        self.baseline = baseline

    def capture_baseline(self, workspace: Workspace) -> dict[str, CheckResult]:
        """Run every check on the untouched workspace and remember the results."""
        self.baseline = {check.name: run_check(workspace, check) for check in self.checks}
        return self.baseline

    def validate(self, workspace: Workspace) -> ValidationOutcome:
        gates: dict[str, bool] = {}
        detail: list[str] = []
        for check in self.checks:
            current = run_check(workspace, check)
            base = self.baseline.get(check.name) if self.baseline else None
            ok, note = self._gate(check, current, base)
            gates[check.name] = ok
            detail.append(f"--- {check.name}: {'PASS' if ok else 'FAIL'} ---")
            if note:
                detail.append(note)
        return ValidationOutcome(passed=all(gates.values()), checks=gates, detail="\n".join(detail))

    @staticmethod
    def _gate(check: Check, current: CheckResult, base: CheckResult | None) -> tuple[bool, str]:
        if current.passed:
            return True, ""

        if base is None or base.passed:
            all_lines = [ln for lines in current.originals.values() for ln in lines]
            return False, _failure_detail(check, current, all_lines, header="")

        if not current.issues:
            return False, (
                "Check failed but produced no recognizable issues (tool crash or "
                "configuration error):\n" + current.output[-OUTPUT_TAIL_CHARS:]
            )

        new_issues = current.issues - base.issues
        if not new_issues:
            return True, f"{sum(base.issues.values())} pre-existing issue(s), none new."

        new_lines: list[str] = []
        for key, count in new_issues.items():
            new_lines.extend(current.originals[key][-count:])
        header = f"{len(new_lines)} new issue(s) introduced (pre-existing ones are ignored):"
        return False, _failure_detail(check, current, new_lines, header=header)


def _failure_detail(check: Check, current: CheckResult, issue_lines: list[str], header: str) -> str:
    parts: list[str] = [header] if header else []
    shown = issue_lines[:MAX_ISSUES_SHOWN]
    parts.extend(shown)
    if len(issue_lines) > len(shown):
        parts.append(f"... and {len(issue_lines) - len(shown)} more")
    if check.include_output_tail or not issue_lines:
        parts.append(current.output[-OUTPUT_TAIL_CHARS:])
    return "\n".join(parts)
