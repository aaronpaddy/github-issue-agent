from src.agent.state import AgentStatus, Attempt, Job, ValidationOutcome


def make_job(**overrides):
    defaults = {
        "id": "job-1",
        "repo": "aaronpaddy/slack-mcp-server",
        "issue_number": 1,
        "issue_title": "Bug",
        "issue_body": "Something is broken",
        "max_attempts": 3,
    }
    defaults.update(overrides)
    return Job(**defaults)


def test_current_attempt_number_starts_at_one():
    job = make_job()
    assert job.current_attempt_number() == 1


def test_attempts_exhausted_respects_cap():
    job = make_job(max_attempts=2)
    assert not job.attempts_exhausted()
    job.attempts.append(Attempt(number=1))
    assert not job.attempts_exhausted()
    job.attempts.append(Attempt(number=2))
    assert job.attempts_exhausted()


def test_finish_sets_status_and_timestamp():
    job = make_job()
    assert job.finished_at is None
    job.finish(AgentStatus.DONE)
    assert job.status == AgentStatus.DONE
    assert job.finished_at is not None


def test_issue_context_includes_comments():
    job = make_job(issue_comments=["please fix soon"])
    ctx = job.issue_context()
    assert "please fix soon" in ctx
    assert "Bug" in ctx


def test_validation_outcome_checks_dict():
    outcome = ValidationOutcome(passed=False, checks={"pytest": True, "ruff": False}, detail="ruff failed")
    assert not outcome.passed
    assert outcome.checks["ruff"] is False
