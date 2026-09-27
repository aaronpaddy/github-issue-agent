"""LLMClient: thin wrapper around the Anthropic SDK's tool-use API.

Deliberately thin — this file's only job is turning our ToolRegistry into
the API's tool schema format and turning API responses into a plain
(text, tool_calls) pair, while enforcing the run's cost budget. All the
actual agent logic (when to stop, how to handle tool results, retry policy)
lives in core.py, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic.types import CacheControlEphemeralParam, ThinkingConfigDisabledParam

from src.agent.cost import CostTracker, Usage


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    raw_content: list[dict[str, Any]]  # for appending back into the conversation


class LLMClient:
    def __init__(
        self,
        model: str = "claude-sonnet-5",
        max_tokens: int = 4096,
        api_key: str | None = None,
        cost: CostTracker | None = None,
    ):
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.cost = cost

    def call(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        if self.cost:
            self.cost.check_budget()

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            # Each loop iteration re-sends the whole conversation; caching the prefix
            # makes those repeats cost a tenth of the normal input price.
            cache_control=CacheControlEphemeralParam(type="ephemeral"),
            # Thinking is off: it bills extra output tokens, and this loop does not
            # replay thinking blocks between turns.
            thinking=ThinkingConfigDisabledParam(type="disabled"),
        )

        if self.cost:
            usage = response.usage
            self.cost.record(
                Usage(
                    input_tokens=usage.input_tokens or 0,
                    output_tokens=usage.output_tokens or 0,
                    cache_write_tokens=usage.cache_creation_input_tokens or 0,
                    cache_read_tokens=usage.cache_read_input_tokens or 0,
                )
            )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_content: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
                raw_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
                raw_content.append(
                    {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                )

        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            raw_content=raw_content,
        )
