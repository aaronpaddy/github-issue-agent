from src.agent.llm import LLMResponse, ToolCall
from src.agent.state import Job
from src.agent.triage import TRIAGE_SCHEMA, triage_issue


class OneShotLLM:
    def __init__(self, response: LLMResponse):
        self.response = response
        self.seen = {}

    def call(self, system, messages, tools):
        self.seen = {"system": system, "messages": messages, "tools": tools}
        return self.response


def reply(**tool_input) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id="t", name="triage", input=tool_input)],
        stop_reason="tool_use",
        raw_content=[],
    )


def make_job(title="Support other currencies", body="Trip next month.") -> Job:
    return Job(id="j", repo="o/r", issue_number=5, issue_title=title, issue_body=body)


def test_a_vague_issue_yields_a_clarification():
    llm = OneShotLLM(
        reply(
            verdict="needs_clarification",
            reason="Could mean symbols, conversion, or per-expense currencies.",
            question="What should 'support' mean: display, conversion, or both?",
        )
    )
    result = triage_issue(llm, make_job())
    assert result is not None
    assert "conversion" in result.question
    assert "symbols" in result.findings


def test_an_actionable_issue_passes_through():
    assert triage_issue(OneShotLLM(reply(verdict="actionable")), make_job()) is None


def test_it_fails_open_when_the_model_does_not_call_the_tool():
    no_tool = LLMResponse(text="hmm", tool_calls=[], stop_reason="end_turn", raw_content=[])
    assert triage_issue(OneShotLLM(no_tool), make_job()) is None


def test_it_fails_open_on_a_clarification_verdict_with_no_question():
    llm = OneShotLLM(reply(verdict="needs_clarification", reason="unclear", question="  "))
    assert triage_issue(llm, make_job()) is None


def test_the_model_sees_only_the_issue_text_and_the_triage_tool():
    llm = OneShotLLM(reply(verdict="actionable"))
    triage_issue(llm, make_job(title="Fix rounding", body="Loses a cent."))
    assert llm.seen["tools"] == [TRIAGE_SCHEMA]
    assert "Fix rounding" in llm.seen["messages"][0]["content"]
    assert "Loses a cent." in llm.seen["messages"][0]["content"]
