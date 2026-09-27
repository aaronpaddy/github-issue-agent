from src.agent.validator import DEFAULT_CHECKS, Validator
from src.agent.workspace import CommandResult, Workspace


class ScriptedWorkspace(Workspace):
    """Returns queued CommandResults per binary, in call order."""

    def __init__(self, scripts: dict[str, list[CommandResult]]):
        self.scripts = {k: list(v) for k, v in scripts.items()}

    def run(self, argv, timeout=120):
        return self.scripts[argv[0]].pop(0)


OK = CommandResult(0, "", "")


def ruff_fail(*lines: str) -> CommandResult:
    return CommandResult(1, "\n".join(lines) + f"\nFound {len(lines)} errors.", "")


def make_scripts(ruff: list[CommandResult]) -> dict[str, list[CommandResult]]:
    # ruff varies per test; the other checks always pass (baseline call + validate call).
    return {"ruff": ruff, "black": [OK, OK], "mypy": [OK, OK], "pytest": [OK, OK]}


def run_baseline_then_validate(ruff_baseline: CommandResult, ruff_now: CommandResult):
    ws = ScriptedWorkspace(make_scripts([ruff_baseline, ruff_now]))
    validator = Validator()
    validator.capture_baseline(ws)
    return validator.validate(ws)


def test_clean_baseline_and_clean_result_passes():
    outcome = run_baseline_then_validate(OK, OK)
    assert outcome.passed
    assert all(outcome.checks.values())


def test_check_clean_at_baseline_must_stay_clean():
    outcome = run_baseline_then_validate(OK, ruff_fail("src/a.py:3:1: F401 unused import"))
    assert not outcome.passed
    assert outcome.checks["ruff"] is False
    assert "F401" in outcome.detail


def test_preexisting_issues_do_not_block_even_when_line_numbers_shift():
    before = ruff_fail("src/a.py:10:1: E501 line too long", "src/b.py:2:1: F401 unused import")
    after = ruff_fail("src/a.py:14:1: E501 line too long", "src/b.py:2:1: F401 unused import")
    outcome = run_baseline_then_validate(before, after)
    assert outcome.passed
    assert "pre-existing" in outcome.detail


def test_fixing_preexisting_issues_passes():
    before = ruff_fail("src/a.py:10:1: E501 line too long", "src/b.py:2:1: F401 unused import")
    after = ruff_fail("src/b.py:2:1: F401 unused import")
    assert run_baseline_then_validate(before, after).passed


def test_new_issue_on_top_of_preexisting_fails_and_reports_only_the_new_one():
    before = ruff_fail("src/a.py:10:1: E501 line too long")
    after = ruff_fail("src/a.py:10:1: E501 line too long", "src/c.py:1:1: F841 unused variable")
    outcome = run_baseline_then_validate(before, after)
    assert not outcome.passed
    assert "F841" in outcome.detail
    assert "E501" not in outcome.detail


def test_failure_with_no_recognizable_issues_fails_even_if_baseline_failed():
    before = ruff_fail("src/a.py:10:1: E501 line too long")
    crash = CommandResult(2, "", "ruff: error: invalid configuration")
    outcome = run_baseline_then_validate(before, crash)
    assert not outcome.passed
    assert "no recognizable issues" in outcome.detail


def test_without_a_baseline_every_check_must_pass():
    ws = ScriptedWorkspace(
        {
            "ruff": [ruff_fail("src/a.py:1:1: F401 unused import")],
            "black": [OK],
            "mypy": [OK],
            "pytest": [OK],
        }
    )
    outcome = Validator().validate(ws)
    assert not outcome.passed


def test_failing_pytest_reports_output_tail():
    tail = "tests/test_x.py:5: AssertionError\nFAILED tests/test_x.py::test_a - assert 1 == 2"
    ws = ScriptedWorkspace(
        {"ruff": [OK], "black": [OK], "mypy": [OK], "pytest": [CommandResult(1, tail, "")]}
    )
    outcome = Validator().validate(ws)
    assert not outcome.passed
    assert "AssertionError" in outcome.detail


def test_default_checks_cover_the_four_tools():
    assert [c.name for c in DEFAULT_CHECKS] == ["ruff", "black", "mypy", "pytest"]
