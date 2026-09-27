"""Tool contract: every tool the model can call has a schema (for the LLM's
tool-use API) and an executor (application code, never the model, actually
runs it). ToolResult is the only thing that ever reaches the model back —
raw exceptions never leak into the conversation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.agent.workspace import Workspace


def truncate(text: str, limit: int, keep_tail: bool = False) -> str:
    """Cap tool output so it doesn't bloat the conversation (and the bill)."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    if keep_tail:
        return f"[... {dropped} earlier characters omitted ...]\n{text[-limit:]}"
    return f"{text[:limit]}\n[... {dropped} more characters omitted ...]"


@dataclass
class ToolResult:
    ok: bool
    output: str
    error: str | None = None

    @classmethod
    def success(cls, output: str) -> ToolResult:
        return cls(ok=True, output=output, error=None)

    @classmethod
    def failure(cls, error: str) -> ToolResult:
        return cls(ok=False, output="", error=error)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Workspace, dict[str, Any]], ToolResult]

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """Holds the tool set exposed to the model for a given run."""

    def __init__(self, tools: list[Tool]):
        self._tools = {t.name: t for t in tools}

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_anthropic_schema() for t in self._tools.values()]

    def execute(self, workspace: Workspace, name: str, tool_input: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(f"unknown tool: {name}")
        try:
            return tool.handler(workspace, tool_input)
        except Exception as e:  # noqa: BLE001 - tool failures must never crash the loop
            return ToolResult.failure(f"{type(e).__name__}: {e}")
