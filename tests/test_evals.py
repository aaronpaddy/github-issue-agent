import json

import pytest

from src.evals.cases import Case, CaseComment, Mutant, load_case, load_cases
from src.evals.fake_github import FakeGitHub
from src.evals.mutation import MutantNotApplicable, apply_mutant, mutated
from src.evals.scoring import CaseResult, classify, judge, render_markdown, summarize


def make_case(**overrides) -> Case:
    base = {
        "id": "c",
        "description": "d",
        "repo": "o/r",
        "base_sha": "abc",
        "issue_number": 1,
        "issue_title": "t",
        "issue_body": "b",
        "expect": "fixed",
    }
    return Case(**{**base, **overrides})


# ---------------------------------------------------------------- the real case files


def test_every_shipped_case_loads_and_is_well_formed():
    cases = load_cases()
    assert len(cases) >= 10
    assert len({c.id for c in cases}) == len(cases)
    for case in cases:
        assert len(case.base_sha) == 40
        if case.expect == "fixed":
            assert case.hidden_test or case.mutants, f"{case.id} has nothing to verify its fix"
            assert (case.directory / "reference.patch").is_file(), f"{case.id} needs a reference"


def test_the_shipped_cases_cover_fixes_and_judgment_calls():
    expectations = {c.expect for c in load_cases()}
    assert expectations == {"fixed", "asks", "no_change"}


def test_a_reply_case_starts_with_the_agents_question_then_a_persons_answer():
    reply = [c for c in load_cases() if c.trigger == "reply"]
    assert reply
    for case in reply:
        assert case.comments[-2].agent_kind == "clarification"
        assert case.comments[-1].agent_kind is None


def test_a_bad_expectation_is_rejected(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "case.json").write_text(json.dumps({
        "id": "x", "description": "", "repo": "o/r", "base_sha": "a", "expect": "explodes",
        "issue": {"number": 1, "title": "t", "body": "b"},
    }))
    with pytest.raises(ValueError, match="expect must be"):
        load_case(d)


def test_a_case_id_must_match_its_directory(tmp_path):
    d = tmp_path / "one"
    d.mkdir()
    (d / "case.json").write_text(json.dumps({
        "id": "two", "description": "", "repo": "o/r", "base_sha": "a", "expect": "asks",
        "issue": {"number": 1, "title": "t", "body": "b"},
    }))
    with pytest.raises(ValueError, match="must match the directory"):
        load_case(d)


# ---------------------------------------------------------------- mutation


def test_a_mutant_replaces_only_the_requested_occurrences():
    source = "x = 1\nx = 1\n"
    assert apply_mutant(source, Mutant("f", "x = 1", "x = 2", count=1)) == "x = 2\nx = 1\n"
    assert apply_mutant(source, Mutant("f", "x = 1", "x = 2", count=2)) == "x = 2\nx = 2\n"


def test_a_mutant_that_finds_nothing_says_so():
    with pytest.raises(MutantNotApplicable):
        apply_mutant("a = 1", Mutant("f", "b = 2", "b = 3"))


def test_mutated_restores_the_file_even_if_the_block_raises(tmp_path):
    (tmp_path / "m.py").write_text("value = 1\n")
    with pytest.raises(RuntimeError), mutated(tmp_path, Mutant("m.py", "1", "2")):
        assert (tmp_path / "m.py").read_text() == "value = 2\n"
        raise RuntimeError("boom")
    assert (tmp_path / "m.py").read_text() == "value = 1\n"


# ---------------------------------------------------------------- the fake github


def test_the_fake_github_serves_the_case_and_records_comments():
    case = make_case(
        issue_number=5,
        issue_title="Support currencies",
        comments=(CaseComment("bot", "Which?", agent_kind="clarification"), CaseComment("ann", "USD")),
    )
    fake = FakeGitHub(case)
    issue = fake.get_issue(999)
    assert (issue.number, issue.title, issue.state, issue.is_pull_request) == (5, "Support currencies", "open", False)
    assert [(c.author, c.agent_kind) for c in issue.comments] == [("bot", "clarification"), ("ann", None)]
    assert fake.find_open_agent_pr(5) is None

    fake.comment_on_issue(5, "hello", "note")
    assert fake.comments == [(5, "hello", "note")]


# ---------------------------------------------------------------- judging


def test_outcomes_map_onto_what_a_case_can_expect():
    assert classify("validated") == "fixed"
    assert classify("draft_pr_opened") == "fixed"
    assert classify("needs_clarification") == "asks"
    assert classify("gave_up") == "asks"
    assert classify("no_change_needed") == "no_change"
    assert classify("declined") == "declined"


def test_doing_the_wrong_kind_of_thing_fails_with_a_plain_reason():
    passed, why = judge(make_case(expect="asks"), "fixed", None, None, None)
    assert not passed
    assert "ask for clarification" in why and "made a fix" in why


def test_a_correct_fix_passes():
    assert judge(make_case(hidden_test="h.py"), "fixed", True, None, True) == (True, "")


def test_a_fix_that_fails_the_hidden_tests_fails():
    passed, why = judge(make_case(hidden_test="h.py"), "fixed", False, None, True)
    assert not passed and "hidden tests failed" in why


def test_tests_that_miss_deliberate_bugs_fail_the_case():
    mutants = tuple(Mutant("f", str(i), "x") for i in range(3))
    case = make_case(mutants=mutants, min_mutants_caught=2)
    assert judge(case, "fixed", None, 2, True)[0]
    passed, why = judge(case, "fixed", None, 1, True)
    assert not passed and "caught only 1 of 3" in why


def test_a_fix_with_no_tests_fails_when_tests_are_required():
    passed, why = judge(make_case(require_tests_added=True), "fixed", True, None, False)
    assert not passed and "no tests" in why


def test_asking_when_a_fix_was_expected_is_a_failure():
    assert not judge(make_case(expect="fixed"), "asks", None, None, None)[0]


# ---------------------------------------------------------------- summary numbers


def result(case_id, expect, got, passed, **kw) -> CaseResult:
    return CaseResult(case_id=case_id, expect=expect, run=1, outcome=got, got=got, passed=passed, **kw)


def test_the_summary_separates_fix_quality_from_judgment_and_counts_false_holdbacks():
    results = [
        result("a", "fixed", "fixed", True, confidence="high", policy_action="open_pr", cost_usd=0.05),
        result("b", "fixed", "fixed", False, confidence="high", policy_action="open_pr", cost_usd=0.05),
        result("c", "fixed", "asks", False, cost_usd=0.01),
        result("d", "asks", "asks", True, cost_usd=0.01),
        result("e", "no_change", "fixed", False, cost_usd=0.05),
    ]
    s = summarize(results)
    assert s["cases_run"] == 5 and s["passed"] == 2
    assert s["fix_cases"] == 3
    assert s["fix_solve_rate"] == pytest.approx(1 / 3)
    assert s["false_holdback_rate"] == pytest.approx(1 / 3)  # only "c" held back
    assert s["judgment_correct_rate"] == pytest.approx(1 / 2)
    assert s["high_confidence_precision"] == pytest.approx(1 / 2)
    assert s["policy_mix"]["open_pr"] == 2
    assert s["total_cost_usd"] == pytest.approx(0.17)


def test_cases_that_were_not_run_do_not_count_against_the_rates():
    results = [result("a", "fixed", "fixed", True), result("b", "fixed", "not_run", False)]
    s = summarize(results)
    assert s["cases_run"] == 1
    assert s["pass_rate"] == 1.0


def test_an_empty_run_has_no_rates_instead_of_dividing_by_zero():
    s = summarize([])
    assert s["pass_rate"] is None and s["avg_cost_usd"] is None


def test_the_report_lists_every_case_and_explains_failures():
    results = [
        result("good", "fixed", "fixed", True, hidden_ok=True, confidence="high", policy_action="open_pr"),
        result("bad", "asks", "fixed", False, failure="expected the agent to ask, but it made a fix"),
    ]
    text = render_markdown(results, summarize(results), {"model": "m", "date": "d", "agent_commit": "abc", "repeat": 1})
    assert "`good`" in text and "`bad`" in text
    assert "**FAIL**" in text
    assert "## Failures" in text and "expected the agent to ask" in text
    assert "| Cases passed | 1 / 2 (50%) |" in text


def test_content_the_issue_tried_to_smuggle_into_the_change_fails_the_case():
    case = make_case(forbidden_in_diff=("evil.example",))
    passed, why = judge(case, "fixed", True, None, True, ("evil.example",))
    assert not passed and "evil.example" in why
    assert judge(case, "fixed", True, None, True, ())[0]


def test_forbidden_content_is_only_checked_on_fix_cases():
    assert judge(make_case(expect="asks"), "asks", None, None, None, ("x",))[0]


def test_the_injection_case_ships_with_what_it_forbids():
    case = next(c for c in load_cases() if c.id == "injection-in-issue")
    assert "evil.example" in case.forbidden_in_diff
    assert "evil.example" in case.issue_body
    assert case.expect == "fixed"


def test_the_report_groups_runs_of_a_case_together_in_order():
    results = [
        result("b", "fixed", "fixed", True),
        result("a", "fixed", "fixed", True),
        CaseResult(case_id="a", expect="fixed", run=2, outcome="fixed", got="fixed", passed=True),
        result("b", "fixed", "fixed", True),
    ]
    results[3].run = 2
    text = render_markdown(results, summarize(results), {"model": "m", "date": "d", "agent_commit": "x", "repeat": 2})
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert [row.split("|")[1].strip() + row.split("|")[2].strip() for row in rows] == ["`a`1", "`a`2", "`b`1", "`b`2"]
