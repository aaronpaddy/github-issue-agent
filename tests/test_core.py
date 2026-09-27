import subprocess

from src.agent.core import FINISH_NUDGE, MAX_TOOL_ITERATIONS_PER_ATTEMPT, AgentLoop
from src.agent.cost import BudgetExceededError
from src.agent.llm import LLMResponse, ToolCall
from src.agent.state import AgentStatus, Job, ValidationOutcome
from src.agent.tools import default_registry
from src.agent.workspace import LocalWorkspace


class FakeLLM:
    """Replays a scripted sequence of responses, one per .call() invocation."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls = 0
        self.seen_messages: list[list[dict]] = []
        self.seen_tool_names: list[str] = []

    def call(self, system, messages, tools):
        self.calls += 1
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_tool_names = [t["name"] for t in tools]
        return self._responses.pop(0)


def edit_call_response(tool_id: str) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[
            ToolCall(
                id=tool_id,
                name="edit_file",
                input={"path": "a.py", "old_string": "return 1", "new_string": "return 2"},
            )
        ],
        stop_reason="tool_use",
        raw_content=[{"type": "tool_use", "id": tool_id, "name": "edit_file", "input": {}}],
    )


def submit_response(tool_id: str, summary: str, confidence: str = "high") -> LLMResponse:
    tool_input = {
        "summary": summary,
        "confidence": confidence,
        "interpretation": "foo should return 2",
        "assumptions": ["only foo is affected"],
    }
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=tool_id, name="submit_result", input=tool_input)],
        stop_reason="tool_use",
        raw_content=[
            {"type": "tool_use", "id": tool_id, "name": "submit_result", "input": tool_input}
        ],
    )


def clarification_response(tool_id: str) -> LLMResponse:
    tool_input = {"question": "Which currencies?", "findings": "The issue names none."}
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=tool_id, name="request_clarification", input=tool_input)],
        stop_reason="tool_use",
        raw_content=[
            {
                "type": "tool_use",
                "id": tool_id,
                "name": "request_clarification",
                "input": tool_input,
            }
        ],
    )


def no_change_response(tool_id: str) -> LLMResponse:
    tool_input = {"reason": "Already fixed in PR #9.", "evidence": "git log shows the commit."}
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=tool_id, name="report_no_change_needed", input=tool_input)],
        stop_reason="tool_use",
        raw_content=[
            {"type": "tool_use", "id": tool_id, "name": "report_no_change_needed", "input": tool_input}
        ],
    )


def text_response(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        raw_content=[{"type": "text", "text": text}],
    )


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
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=False)
    return LocalWorkspace(tmp_path)


def passing(ws):
    return ValidationOutcome(passed=True, checks={"pytest": True}, detail="")


def failing(ws):
    return ValidationOutcome(passed=False, checks={"pytest": False}, detail="still failing")


def test_agent_loop_succeeds_and_records_the_assessment(tmp_path):
    llm = FakeLLM([edit_call_response("t1"), submit_response("t2", "changed return value to 2")])
    loop = AgentLoop(llm=llm, tools=default_registry(), validate_fn=passing)
    job = make_job()
    result = loop.run(job, make_workspace(tmp_path))

    assert result.success
    assert job.status == AgentStatus.DONE
    assert len(job.attempts) == 1
    assert llm.calls == 2
    assert job.assessment is not None
    assert job.assessment.confidence == "high"
    assert job.assessment.assumptions == ["only foo is affected"]
    assert result.summary == "changed return value to 2"


def test_the_model_is_offered_the_terminal_tools(tmp_path):
    llm = FakeLLM([edit_call_response("t1"), submit_response("t2", "done")])
    AgentLoop(llm=llm, tools=default_registry(), validate_fn=passing).run(
        make_job(), make_workspace(tmp_path)
    )
    assert "submit_result" in llm.seen_tool_names
    assert "request_clarification" in llm.seen_tool_names
    assert "edit_file" in llm.seen_tool_names


def test_agent_loop_retries_then_escalates(tmp_path):
    llm = FakeLLM(
        [
            edit_call_response("t1"),
            submit_response("t2", "attempt 1"),
            edit_call_response("t3"),
            submit_response("t4", "attempt 2"),
        ]
    )
    loop = AgentLoop(llm=llm, tools=default_registry(), validate_fn=failing)
    job = make_job()  # max_attempts=2
    result = loop.run(job, make_workspace(tmp_path))

    assert not result.success
    assert job.status == AgentStatus.ESCALATED
    assert len(job.attempts) == 2
    assert "still failing" in (job.escalation_reason or "")


def test_a_failure_prompt_follows_the_tool_result_for_submit_result(tmp_path):
    # The API needs every tool_use answered before the next turn, so the loop must reply to
    # submit_result before it reports a validation failure.
    llm = FakeLLM(
        [
            edit_call_response("t1"),
            submit_response("t2", "attempt 1"),
            submit_response("t3", "attempt 2"),
        ]
    )
    AgentLoop(llm=llm, tools=default_registry(), validate_fn=failing).run(
        make_job(), make_workspace(tmp_path)
    )
    third_call = llm.seen_messages[2]
    answered = [
        block["tool_use_id"]
        for m in third_call
        if m["role"] == "user" and isinstance(m["content"], list)
        for block in m["content"]
        if block["type"] == "tool_result"
    ]
    assert "t2" in answered
    assert "Validation failed" in third_call[-1]["content"]


def test_agent_loop_never_exceeds_max_attempts(tmp_path):
    responses = []
    for i in range(5):
        responses += [edit_call_response(f"e{i}"), submit_response(f"s{i}", f"attempt {i}")]
    llm = FakeLLM(responses)
    job = make_job()
    job.max_attempts = 1
    AgentLoop(llm=llm, tools=default_registry(), validate_fn=failing).run(
        job, make_workspace(tmp_path)
    )
    assert len(job.attempts) == 1


def test_stopping_without_a_result_gets_one_nudge_then_no_assessment(tmp_path):
    llm = FakeLLM(
        [edit_call_response("t1"), text_response("all done"), text_response("really done")]
    )
    job = make_job()
    result = AgentLoop(llm=llm, tools=default_registry(), validate_fn=passing).run(
        job, make_workspace(tmp_path)
    )

    assert result.success  # validation still passed
    assert job.assessment is None  # but the policy will treat confidence as unknown
    assert llm.seen_messages[2][-1]["content"] == FINISH_NUDGE
    assert llm.calls == 3


def test_nudge_is_enough_when_the_model_then_submits(tmp_path):
    llm = FakeLLM(
        [edit_call_response("t1"), text_response("all done"), submit_response("t2", "done")]
    )
    job = make_job()
    AgentLoop(llm=llm, tools=default_registry(), validate_fn=passing).run(
        job, make_workspace(tmp_path)
    )
    assert job.assessment is not None


def test_request_clarification_stops_without_validating(tmp_path):
    def must_not_run(ws):
        raise AssertionError("validation must not run when the agent asks for clarification")

    llm = FakeLLM([clarification_response("t1")])
    job = make_job()
    result = AgentLoop(llm=llm, tools=default_registry(), validate_fn=must_not_run).run(
        job, make_workspace(tmp_path)
    )

    assert not result.success
    assert job.status == AgentStatus.NEEDS_CLARIFICATION
    assert job.clarification is not None
    assert job.clarification.question == "Which currencies?"
    assert job.attempts == []


def test_report_no_change_needed_stops_without_validating_or_retrying(tmp_path):
    def must_not_run(ws):
        raise AssertionError("validation must not run when no change is needed")

    llm = FakeLLM([no_change_response("t1")])
    job = make_job()
    result = AgentLoop(llm=llm, tools=default_registry(), validate_fn=must_not_run).run(
        job, make_workspace(tmp_path)
    )

    assert not result.success
    assert job.status == AgentStatus.NO_CHANGE_NEEDED
    assert job.no_change is not None
    assert job.no_change.reason == "Already fixed in PR #9."
    assert job.attempts == []
    assert llm.calls == 1
    assert "report_no_change_needed" in llm.seen_tool_names


def test_the_no_changes_retry_prompt_points_at_the_honest_exits(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True)

    llm = FakeLLM([submit_response("t1", "nothing"), no_change_response("t2")])
    job = make_job()
    AgentLoop(llm=llm, tools=default_registry(), validate_fn=passing).run(job, LocalWorkspace(tmp_path))

    retry_prompt = llm.seen_messages[1][-1]["content"]
    assert "report_no_change_needed" in retry_prompt
    assert "request_clarification" in retry_prompt
    assert job.status == AgentStatus.NO_CHANGE_NEEDED


def test_agent_loop_rejects_a_pass_with_no_changes(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True)

    llm = FakeLLM([submit_response("t1", "nothing to change")])
    job = make_job()
    job.max_attempts = 1
    result = AgentLoop(llm=llm, tools=default_registry(), validate_fn=passing).run(
        job, LocalWorkspace(tmp_path)
    )

    assert not result.success
    assert job.status == AgentStatus.ESCALATED
    assert "No files were changed" in (job.escalation_reason or "")


def test_agent_loop_escalates_when_the_budget_runs_out(tmp_path):
    class BrokeLLM:
        def call(self, system, messages, tools):
            raise BudgetExceededError("Run budget of $1.00 reached")

    loop = AgentLoop(llm=BrokeLLM(), tools=default_registry(), validate_fn=passing)
    job = make_job()
    result = loop.run(job, make_workspace(tmp_path))

    assert not result.success
    assert job.status == AgentStatus.ESCALATED
    assert "budget" in (job.escalation_reason or "").lower()
    assert job.attempts == []


def test_escalation_includes_the_agents_own_explanation(tmp_path):
    llm = FakeLLM([submit_response("t1", "The fix did not take.")])
    job = make_job()
    job.max_attempts = 1
    AgentLoop(llm=llm, tools=default_registry(), validate_fn=failing).run(
        job, make_workspace(tmp_path)
    )
    assert "The fix did not take." in (job.escalation_reason or "")


def read_call_response(tool_id: str) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=tool_id, name="read_file", input={"path": "a.py"})],
        stop_reason="tool_use",
        raw_content=[{"type": "tool_use", "id": tool_id, "name": "read_file", "input": {"path": "a.py"}}],
    )


def test_hitting_the_tool_cap_still_ends_with_a_self_assessment(tmp_path):
    busy = [read_call_response(f"r{i}") for i in range(MAX_TOOL_ITERATIONS_PER_ATTEMPT)]
    llm = FakeLLM([*busy, submit_response("final", "Got partway.", confidence="low")])
    job = make_job()
    AgentLoop(llm=llm, tools=default_registry(), validate_fn=passing).run(job, make_workspace(tmp_path))

    assert job.assessment is not None
    assert job.assessment.confidence == "low"
    assert llm.calls == MAX_TOOL_ITERATIONS_PER_ATTEMPT + 1
    # the last round is limited to the terminal tools: no more exploring
    assert set(llm.seen_tool_names) == {"submit_result", "request_clarification", "report_no_change_needed"}


def test_hitting_the_tool_cap_and_still_not_submitting_leaves_no_assessment(tmp_path):
    busy = [read_call_response(f"r{i}") for i in range(MAX_TOOL_ITERATIONS_PER_ATTEMPT)]
    llm = FakeLLM([*busy, text_response("still going")])
    job = make_job()
    AgentLoop(llm=llm, tools=default_registry(), validate_fn=passing).run(job, make_workspace(tmp_path))
    assert job.assessment is None
