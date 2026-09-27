from src.agent.policy import Action, DiffStats, Limits, decide, is_test_path
from src.agent.state import Assessment


def assessment(confidence="high", **kwargs):
    return Assessment(
        summary="s", confidence=confidence, interpretation="i", assumptions=kwargs.get("assumptions", [])
    )


CLEAN = DiffStats(files=("src/pkg/split.py", "tests/test_split.py"), lines_added=20, lines_deleted=2)


def test_high_confidence_and_clean_signals_open_a_normal_pr_that_closes_the_issue():
    d = decide(assessment("high"), attempts=1, stats=CLEAN)
    assert d.action == Action.OPEN_PR
    assert d.closes_issue
    assert d.reasons == []


def test_missing_assessment_means_comment_only():
    d = decide(None, attempts=1, stats=CLEAN)
    assert d.action == Action.COMMENT_ONLY
    assert not d.closes_issue
    assert "unknown" in d.reasons[0]


def test_low_confidence_means_comment_only():
    d = decide(assessment("low"), attempts=1, stats=CLEAN)
    assert d.action == Action.COMMENT_ONLY
    assert not d.closes_issue


def test_medium_confidence_is_a_draft_that_only_references_the_issue():
    d = decide(assessment("medium"), attempts=1, stats=CLEAN)
    assert d.action == Action.OPEN_DRAFT_PR
    assert not d.closes_issue
    assert any("medium confidence" in r for r in d.reasons)


def test_needing_a_retry_downgrades_to_a_draft():
    d = decide(assessment("high"), attempts=2, stats=CLEAN)
    assert d.action == Action.OPEN_DRAFT_PR
    assert any("2 attempts" in r for r in d.reasons)


def test_source_changes_without_tests_downgrade_to_a_draft():
    stats = DiffStats(files=("src/pkg/split.py",), lines_added=5, lines_deleted=1)
    d = decide(assessment("high"), attempts=1, stats=stats)
    assert d.action == Action.OPEN_DRAFT_PR
    assert any("no tests" in r for r in d.reasons)


def test_docs_only_changes_do_not_need_tests():
    stats = DiffStats(files=("README.md",), lines_added=3, lines_deleted=1)
    assert decide(assessment("high"), attempts=1, stats=stats).action == Action.OPEN_PR


def test_a_large_diff_downgrades_to_a_draft():
    many_files = tuple(f"tests/test_{i}.py" for i in range(9))
    d = decide(assessment("high"), 1, DiffStats(files=many_files, lines_added=10))
    assert d.action == Action.OPEN_DRAFT_PR
    assert any("9 files" in r for r in d.reasons)

    long_diff = DiffStats(files=("src/a.py", "tests/test_a.py"), lines_added=400)
    d = decide(assessment("high"), 1, long_diff)
    assert any("400 lines" in r for r in d.reasons)


def test_limits_are_configurable():
    stats = DiffStats(files=("src/a.py", "tests/test_a.py"), lines_added=50)
    assert decide(assessment("high"), 1, stats, Limits(max_changed_lines=10)).action == Action.OPEN_DRAFT_PR


def test_every_doubt_is_reported_not_just_the_first():
    stats = DiffStats(files=("src/a.py",), lines_added=5)
    d = decide(assessment("medium"), attempts=3, stats=stats)
    assert d.action == Action.OPEN_DRAFT_PR
    assert len(d.reasons) == 3


def test_test_path_detection():
    assert is_test_path("tests/test_split.py")
    assert is_test_path("pkg/test_models.py")
    assert is_test_path("pkg/models_test.py")
    assert is_test_path("web/src/app.test.ts")
    assert not is_test_path("src/pkg/split.py")
    assert not is_test_path("README.md")
