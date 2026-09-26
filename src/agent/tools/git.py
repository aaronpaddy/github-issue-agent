"""git_diff / git_status: read-only self-review tools.

Lets the model inspect its own changes before deciding it's done — mirrors
how a human developer reviews `git diff` before opening a PR.
"""

from __future__ import annotations

from typing import Any

from src.agent.tools.base import Tool, ToolResult
from src.agent.workspace import Workspace


def _git_diff(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    result = ws.run(["git", "diff"])
    if not result.ok:
        return ToolResult.failure(result.stderr or "git diff failed")
    return ToolResult.success(result.stdout or "(no changes)")


def _git_status(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    result = ws.run(["git", "status", "--short"])
    if not result.ok:
        return ToolResult.failure(result.stderr or "git status failed")
    return ToolResult.success(result.stdout or "(clean)")


GIT_DIFF = Tool(
    name="git_diff",
    description="Show the current uncommitted diff in the workspace.",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_git_diff,
)

GIT_STATUS = Tool(
    name="git_status",
    description="Show the current git status (short form) of the workspace.",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_git_status,
)
