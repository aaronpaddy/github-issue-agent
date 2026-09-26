from src.agent.core import AgentLoop
from src.agent.llm import LLMResponse, ToolCall
from src.agent.state import AgentStatus, Job, ValidationOutcome
from src.agent.tools import default_registry
from src.agent.workspace import LocalWorkspace


class FakeLLM:
    """Replays a scripted sequence of responses, one per .call() invocation."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls = 0

    def call(self, system, messages, tools):
        self.calls += 1
        return self._responses.pop(0)


def edit_call_response(tool_id: str) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=tool_id, name="edit_file", input={
            "path": "a.py", "old_string": "return 1", "new_string": "return 2",
        })],
        stop_reason="tool_use",
        raw_content=[{"type": "tool_use", "id": tool_id, "name": "edit_file", "input": {}}],
    )


def summary_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], stop_reason="end_turn", raw_content=[{"type": "text", "text": text}])


def make_job() -> Job:
    return Job(
        id="job-1",
        repo="aaronpaddy/slack-mcp-server",
        issue_number=42,
        issue_title="foo() returns wrong value",
        issue_body="foo() should return 2, not 1",
        max_attempts=2,
    )


def make_workspace(tmp_path):
    import subprocess

    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    return LocalWorkspace(tmp_path)


def test_agent_loop_succeeds_on_first_attempt(tmp_path):
    llm = FakeLLM([edit_call_response("t1"), summary_response("changed return value to 2")])
    validate_fn = lambda ws: ValidationOutcome(passed=True, checks={"pytest": True}, detail="")

    loop = AgentLoop(llm=llm, tools=default_registry(), validate_fn=validate_fn)
    job = make_job()
    result = loop.run(job, make_workspace(tmp_path))

    assert result.success
    assert job.status == AgentStatus.DONE
    assert len(job.attempts) == 1
    assert llm.calls == 2


def test_agent_loop_retries_then_escalates(tmp_path):
    llm = FakeLLM([
        edit_call_response("t1"), summary_response("attempt 1"),
        edit_call_response("t2"), summary_response("attempt 2"),
    ])
    validate_fn = lambda ws: ValidationOutcome(passed=False, checks={"pytest": False}, detail="still failing")

    loop = AgentLoop(llm=llm, tools=default_registry(), validate_fn=validate_fn)
    job = make_job()  # max_attempts=2
    result = loop.run(job, make_workspace(tmp_path))

    assert not result.success
    assert job.status == AgentStatus.ESCALATED
    assert len(job.attempts) == 2
    assert job.escalation_reason is not None
    assert "still failing" in job.escalation_reason


def test_agent_loop_never_exceeds_max_attempts(tmp_path):
    # Even if validation always fails, the loop must stop at exactly max_attempts.
    responses = []
    for i in range(5):
        responses += [edit_call_response(f"t{i}"), summary_response(f"attempt {i}")]
    llm = FakeLLM(responses)
    validate_fn = lambda ws: ValidationOutcome(passed=False, checks={"pytest": False}, detail="nope")

    loop = AgentLoop(llm=llm, tools=default_registry(), validate_fn=validate_fn)
    job = make_job()
    job.max_attempts = 1
    loop.run(job, make_workspace(tmp_path))

    assert len(job.attempts) == 1
