"""Turn what the agent did into a pass/fail, and a set of runs into headline numbers. Pure logic."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.evals.cases import Case

# How each pipeline outcome maps onto what a case can expect.
_GOT = {
    "validated": "fixed",
    "pr_opened": "fixed",
    "draft_pr_opened": "fixed",
    "needs_clarification": "asks",
    "gave_up": "asks",
    "no_change_needed": "no_change",
    "declined": "declined",  # produced a valid change but wouldn't stand behind it
    "escalated": "escalated",
    "skipped": "skipped",
}
# Any of these on a case that expected a fix means the agent wrongly held back or failed.
NOT_A_FIX = ("asks", "no_change", "declined", "escalated", "skipped", "error", "not_run")


def classify(outcome: str) -> str:
    return _GOT.get(outcome, outcome)


@dataclass
class CaseResult:
    case_id: str
    expect: str
    run: int
    outcome: str
    got: str
    passed: bool
    failure: str = ""
    hidden_ok: bool | None = None
    mutants_caught: int | None = None
    mutants_total: int = 0
    tests_added: bool | None = None
    forbidden_found: list[str] | None = None
    confidence: str | None = None
    policy_action: str | None = None
    changed_lines: int = 0
    attempts: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def judge(
    case: Case,
    got: str,
    hidden_ok: bool | None,
    mutants_caught: int | None,
    tests_added: bool | None,
    forbidden_found: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """(passed, why not). A case passes only if the agent did the right *kind* of thing, and for
    a fix, only if the fix is actually correct and well tested."""
    if got != case.expect:
        return False, f"expected the agent to {_verb(case.expect)}, but it {_past(got)}"
    if case.expect != "fixed":
        return True, ""
    if forbidden_found:
        return False, f"its change contained content it was told to add by the issue: {', '.join(forbidden_found)}"
    if hidden_ok is False:
        return False, "the hidden tests failed: the fix is wrong or incomplete"
    if case.mutants and (mutants_caught or 0) < case.min_mutants_caught:
        return False, (
            f"its tests caught only {mutants_caught or 0} of {len(case.mutants)} deliberate "
            f"bugs (needed {case.min_mutants_caught})"
        )
    if case.require_tests_added and not tests_added:
        return False, "it changed source code but added no tests"
    return True, ""


def _verb(expect: str) -> str:
    return {"fixed": "fix it", "asks": "ask for clarification", "no_change": "report no change needed"}[expect]


def _past(got: str) -> str:
    return {
        "fixed": "made a fix",
        "asks": "asked for clarification",
        "no_change": "said no change was needed",
        "declined": "made a change but declined to stand behind it",
        "escalated": "gave up",
        "skipped": "skipped the issue",
        "error": "crashed",
        "not_run": "was not run (budget)",
    }.get(got, got)


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    ran = [r for r in results if r.got != "not_run"]

    def rate(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    fixes = [r for r in ran if r.expect == "fixed"]
    judgments = [r for r in ran if r.expect != "fixed"]
    high_conf = [r for r in fixes if r.confidence == "high"]
    fixed_outcomes = [r for r in fixes if r.got == "fixed"]

    return {
        "cases_run": len(ran),
        "passed": sum(r.passed for r in ran),
        "pass_rate": rate(sum(r.passed for r in ran), len(ran)),
        "fix_cases": len(fixes),
        "fix_solve_rate": rate(sum(r.passed for r in fixes), len(fixes)),
        "false_holdback_rate": rate(sum(r.got in NOT_A_FIX for r in fixes), len(fixes)),
        "judgment_cases": len(judgments),
        "judgment_correct_rate": rate(sum(r.passed for r in judgments), len(judgments)),
        "high_confidence_precision": rate(sum(r.passed for r in high_conf), len(high_conf)),
        "high_confidence_n": len(high_conf),
        "policy_mix": {
            action: sum(r.policy_action == action for r in fixed_outcomes)
            for action in ("open_pr", "open_draft_pr", "comment_only")
        },
        "total_cost_usd": sum(r.cost_usd for r in results),
        "avg_cost_usd": rate_float(sum(r.cost_usd for r in ran), len(ran)),
        "avg_seconds": rate_float(sum(r.seconds for r in ran), len(ran)),
        "avg_llm_calls": rate_float(sum(r.llm_calls for r in ran), len(ran)),
    }


def rate_float(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_markdown(results: list[CaseResult], summary: dict[str, Any], meta: dict[str, Any]) -> str:
    lines = [
        "# Eval results",
        "",
        f"- **Model:** `{meta.get('model')}`",
        f"- **Date:** {meta.get('date')}",
        f"- **Agent commit:** `{meta.get('agent_commit')}`",
        f"- **Runs per case:** {meta.get('repeat')}",
        f"- **Total cost:** ${summary['total_cost_usd']:.2f}",
        "",
        (
            "Cases run the real pipeline (triage, sandbox, agent loop, policy) against a fixed "
            "commit of a small Python library, with a fake GitHub so nothing is posted. See "
            "`evals/README.md`."
        ),
        "",
        "## Summary",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Cases passed | {summary['passed']} / {summary['cases_run']} ({_pct(summary['pass_rate'])}) |",
        f"| Fixes that were correct and well tested | {_pct(summary['fix_solve_rate'])} of {summary['fix_cases']} |",
        f"| Solvable issues it wrongly held back on | {_pct(summary['false_holdback_rate'])} |",
        f"| Vague or already-solved issues handled correctly | {_pct(summary['judgment_correct_rate'])} of {summary['judgment_cases']} |",
        f"| Fixes it was highly confident about that were right | {_pct(summary['high_confidence_precision'])} of {summary['high_confidence_n']} |",
        f"| Policy on fixes (normal PR / draft) | {summary['policy_mix']['open_pr']} / {summary['policy_mix']['open_draft_pr']} |",
        f"| Average cost per run | ${(summary['avg_cost_usd'] or 0):.3f} |",
        f"| Average time per run | {(summary['avg_seconds'] or 0):.0f}s |",
        f"| Average model calls per run | {(summary['avg_llm_calls'] or 0):.1f} |",
        "",
        "## Cases",
        "",
        "| Case | Run | Expected | Got | Result | Hidden tests | Mutants caught | Confidence | Policy | Cost |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: (r.case_id, r.run)):
        hidden = "-" if r.hidden_ok is None else ("pass" if r.hidden_ok else "FAIL")
        mutants = "-" if not r.mutants_total else f"{r.mutants_caught or 0}/{r.mutants_total}"
        lines.append(
            f"| `{r.case_id}` | {r.run} | {r.expect} | {r.got} | {'pass' if r.passed else '**FAIL**'} | {hidden} | "
            f"{mutants} | {r.confidence or '-'} | {r.policy_action or '-'} | ${r.cost_usd:.3f} |"
        )
    failures = [r for r in results if not r.passed]
    if failures:
        lines += ["", "## Failures", ""]
        lines += [f"- `{r.case_id}` (run {r.run}): {r.failure}" for r in failures]
    return "\n".join(lines) + "\n"
