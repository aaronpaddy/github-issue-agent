"""Filesystem tools: list_files, read_file, create_file, edit_file.

edit_file takes (path, old_string, new_string) rather than a raw full-file
overwrite. old_string must match exactly once in the file. This is a
deliberate constraint: it forces the model to show its work (it must quote
the exact text it's changing) and makes a runaway "rewrite the whole file
badly" failure mode structurally impossible.
"""

from __future__ import annotations

from typing import Any

from src.agent.tools.base import Tool, ToolResult, truncate
from src.agent.workspace import Workspace

MAX_LIST_ENTRIES = 300
MAX_READ_CHARS = 12000


def _list_files(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    path = args.get("path", ".")
    files = ws.list_files(path)
    if not files:
        return ToolResult.success(f"(no files found under '{path}')")
    shown = files[:MAX_LIST_ENTRIES]
    output = "\n".join(shown)
    if len(files) > len(shown):
        output += f"\n[... {len(files) - len(shown)} more files; list a subdirectory to narrow down]"
    return ToolResult.success(output)


def _read_file(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    try:
        content = ws.read_file(path)
    except FileNotFoundError:
        return ToolResult.failure(f"file not found: {path}")
    lines = content.splitlines()
    total = len(lines)
    if total == 0:
        return ToolResult.success("(empty file)")
    start = max(int(args.get("start_line", 1)), 1)
    end = min(int(args.get("end_line", total)), total)
    if start > end:
        return ToolResult.failure(f"start_line {start} is past end_line {end} (file has {total} lines)")
    body = "\n".join(f"{n}\t{lines[n - 1]}" for n in range(start, end + 1))
    body = truncate(body, MAX_READ_CHARS)
    return ToolResult.success(f"[{path}: lines {start}-{end} of {total}]\n{body}")


def _create_file(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    content = args.get("content", "")
    resolved = ws.resolve(path)
    if resolved.exists():
        return ToolResult.failure(f"file already exists, use edit_file instead: {path}")
    ws.write_file(path, content)
    return ToolResult.success(f"created {path} ({len(content)} bytes)")


def _edit_file(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    old_string = args["old_string"]
    new_string = args["new_string"]

    try:
        content = ws.read_file(path)
    except FileNotFoundError:
        return ToolResult.failure(f"file not found: {path}")

    count = content.count(old_string)
    if count == 0:
        return ToolResult.failure(
            f"old_string not found in {path} — it must match the file exactly, "
            "including whitespace"
        )
    if count > 1:
        return ToolResult.failure(
            f"old_string matches {count} locations in {path} — include more "
            "surrounding context to make it unique"
        )

    new_content = content.replace(old_string, new_string, 1)
    ws.write_file(path, new_content)
    return ToolResult.success(f"edited {path}")


LIST_FILES = Tool(
    name="list_files",
    description="List files under a directory in the repo (recursive). Use path='.' for the repo root.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory path relative to repo root"}},
        "required": [],
    },
    handler=_list_files,
)

READ_FILE = Tool(
    name="read_file",
    description=(
        "Read a file with line numbers, relative to the repo root. Optionally pass "
        "start_line/end_line to read only a range; large reads are truncated, so prefer "
        "ranges once you know where the relevant code is."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "description": "First line to read (1-based)"},
            "end_line": {"type": "integer", "description": "Last line to read (inclusive)"},
        },
        "required": ["path"],
    },
    handler=_read_file,
)

CREATE_FILE = Tool(
    name="create_file",
    description="Create a new file with the given content. Fails if the file already exists.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    handler=_create_file,
)

EDIT_FILE = Tool(
    name="edit_file",
    description=(
        "Edit an existing file by replacing an exact, unique excerpt (old_string) "
        "with new_string. old_string must match the file's current content exactly, "
        "including whitespace, and must be unique in the file — include enough "
        "surrounding context if it isn't."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    handler=_edit_file,
)
