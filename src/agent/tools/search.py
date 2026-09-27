"""search_code: text/symbol search across the repo, backed by grep.

Kept separate from list_files/read_file because search is how the agent
narrows a whole repo down to the handful of files actually relevant to an
issue — the "autonomous investigation" goal from the brief.
"""

from __future__ import annotations

from typing import Any

from src.agent.tools.base import Tool, ToolResult, truncate
from src.agent.workspace import Workspace

_EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}
MAX_SEARCH_CHARS = 8000


def _search_code(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    query = args["query"]
    exclude = _EXCLUDE_DIRS
    argv = [
        "grep",
        "-rn",
        "--include=*.py",
        *[f"--exclude-dir={d}" for d in exclude],
        query,
        ".",
    ]
    result = ws.run(argv)
    if result.returncode == 1 and not result.stdout.strip():
        return ToolResult.success(f"no matches for '{query}'")
    if result.returncode not in (0, 1):
        return ToolResult.failure(result.stderr or "search failed")
    lines = result.stdout.strip().splitlines()
    truncated = lines[:200]
    output = truncate("\n".join(truncated), MAX_SEARCH_CHARS)
    if len(lines) > 200:
        output += f"\n... ({len(lines) - 200} more matches truncated)"
    return ToolResult.success(output)


SEARCH_CODE = Tool(
    name="search_code",
    description=(
        "Search Python source files in the repo for a text pattern (grep-style, "
        "regex supported). Returns matching file:line:content. Use this to find "
        "where a symbol, error message, or piece of logic lives."
    ),
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Text or regex pattern to search for"}},
        "required": ["query"],
    },
    handler=_search_code,
)
