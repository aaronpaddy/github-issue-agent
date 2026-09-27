"""Token and dollar accounting with a hard per-run budget.

Every model call is priced from the API's own usage numbers, and the run is
stopped before the next call once the budget is spent. The check happens
before a call, so a run can overshoot by at most one call's cost.
"""

from __future__ import annotations

from dataclasses import dataclass

# USD per million tokens (input, output). Update when models or prices change.
PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
# Unknown models are billed at the highest known rate so the budget stays safe.
FALLBACK_PRICING = max(PRICING.values())

CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute cache writes cost 1.25x base input
CACHE_READ_MULTIPLIER = 0.10  # cache reads cost 0.1x base input


class BudgetExceededError(Exception):
    """Raised before a model call when the run's dollar budget is spent."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


class CostTracker:
    def __init__(self, model: str, budget_usd: float):
        self.model = model
        self.budget_usd = budget_usd
        self.calls = 0
        self.usage = Usage()
        self.spent_usd = 0.0
        self._input_price, self._output_price = PRICING.get(model, FALLBACK_PRICING)

    def check_budget(self) -> None:
        if self.spent_usd >= self.budget_usd:
            raise BudgetExceededError(
                f"Run budget of ${self.budget_usd:.2f} reached "
                f"(${self.spent_usd:.2f} spent over {self.calls} model calls)."
            )

    def record(self, usage: Usage) -> float:
        cost = (
            usage.input_tokens * self._input_price
            + usage.cache_write_tokens * self._input_price * CACHE_WRITE_MULTIPLIER
            + usage.cache_read_tokens * self._input_price * CACHE_READ_MULTIPLIER
            + usage.output_tokens * self._output_price
        ) / 1_000_000
        self.calls += 1
        self.usage.input_tokens += usage.input_tokens
        self.usage.output_tokens += usage.output_tokens
        self.usage.cache_write_tokens += usage.cache_write_tokens
        self.usage.cache_read_tokens += usage.cache_read_tokens
        self.spent_usd += cost
        return cost

    def summary(self) -> str:
        u = self.usage
        return (
            f"{self.calls} calls, ${self.spent_usd:.4f} of ${self.budget_usd:.2f} budget "
            f"(input {u.input_tokens:,}, cache write {u.cache_write_tokens:,}, "
            f"cache read {u.cache_read_tokens:,}, output {u.output_tokens:,} tokens)"
        )
