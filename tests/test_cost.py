from types import SimpleNamespace

import pytest

from src.agent.cost import BudgetExceededError, CostTracker, Usage
from src.agent.llm import LLMClient


def test_cost_is_priced_per_token_class():
    tracker = CostTracker("claude-sonnet-5", budget_usd=10)  # $2 in / $10 out per M
    cost = tracker.record(
        Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_write_tokens=1_000_000, cache_read_tokens=1_000_000)
    )
    # 2.00 input + 2.50 cache write + 0.20 cache read + 10.00 output
    assert cost == pytest.approx(14.70)
    assert tracker.spent_usd == pytest.approx(14.70)
    assert tracker.calls == 1


def test_unknown_model_is_priced_at_the_highest_known_rate():
    unknown = CostTracker("some-future-model", budget_usd=10)
    priciest = CostTracker("claude-opus-5", budget_usd=10)
    usage = Usage(input_tokens=1000, output_tokens=1000)
    assert unknown.record(usage) == pytest.approx(priciest.record(usage))


def test_budget_blocks_the_next_call_once_spent():
    tracker = CostTracker("claude-sonnet-5", budget_usd=0.01)
    tracker.check_budget()  # nothing spent yet
    tracker.record(Usage(output_tokens=2000))  # $0.02
    with pytest.raises(BudgetExceededError):
        tracker.check_budget()


def fake_response(**usage):
    defaults = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": None, "cache_read_input_tokens": None}
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="done")],
        stop_reason="end_turn",
        usage=SimpleNamespace(**{**defaults, **usage}),
    )


def make_client(tracker, response):
    client = LLMClient(model="claude-sonnet-5", api_key="test-key", cost=tracker)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return response

    client._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return client, calls


def test_llm_call_records_usage_and_enables_caching_without_thinking():
    tracker = CostTracker("claude-sonnet-5", budget_usd=1)
    client, calls = make_client(tracker, fake_response(input_tokens=100, output_tokens=50, cache_read_input_tokens=900))
    result = client.call(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])

    assert result.text == "done"
    assert tracker.usage.cache_read_tokens == 900
    assert tracker.usage.input_tokens == 100
    assert calls[0]["cache_control"] == {"type": "ephemeral"}
    assert calls[0]["thinking"] == {"type": "disabled"}


def test_llm_call_refuses_to_call_the_api_when_over_budget():
    tracker = CostTracker("claude-sonnet-5", budget_usd=0.001)
    client, calls = make_client(tracker, fake_response(output_tokens=1000))
    client.call(system="s", messages=[], tools=[])  # spends $0.01
    with pytest.raises(BudgetExceededError):
        client.call(system="s", messages=[], tools=[])
    assert len(calls) == 1
