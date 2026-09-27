"""run_tests / run_command: the only tools that execute anything.

run_command is deliberately NOT arbitrary shell. It accepts only a fixed
allowlist of (binary, first-arg) prefixes — the validation tools this repo
already uses. This is the concrete enforcement of "no unconstrained shell
access from the model's perspective": the application decides what's
executable, not the model, and anything off the list is rejected before a
subprocess is ever spawned.
"""

from __future__ import annotations

from typing import Any

from src.agent.tools.base import Tool, ToolResult, truncate
from src.agent.workspace import Workspace

# (binary, allowed first args...) — empty tuple means any args after the binary are fine,
# as long as they don't contain shell metacharacters (enforced separately).
ALLOWED_COMMANDS: dict[str, set[str] | None] = {
    "pytest": None,
    "ruff": {"check", "format"},
    "black": None,
    "mypy": None,
    "git": {"diff", "status", "log", "show", "branch"},
}

_FORBIDDEN_CHARS = set(";|&$`\n<>")
MAX_COMMAND_OUTPUT_CHARS = 6000


def _validate_argv(argv: list[str]) -> str | None:
    if not argv:
        return "empty command"
    binary = argv[0]
    if binary not in ALLOWED_COMMANDS:
        return (
            f"command '{binary}' is not allowlisted. Allowed: "
            f"{', '.join(sorted(ALLOWED_COMMANDS))}"
        )
    allowed_subcommands = ALLOWED_COMMANDS[binary]
    if allowed_subcommands is not None and len(argv) > 1 and argv[1] not in allowed_subcommands:
        return (
            f"'{binary} {argv[1]}' is not allowlisted. Allowed subcommands for "
            f"{binary}: {', '.join(sorted(allowed_subcommands))}"
        )
    for token in argv:
        if _FORBIDDEN_CHARS & set(token):
            return f"argument contains forbidden shell metacharacters: {token!r}"
    return None


def _run_tests(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    target = args.get("target", "")
    argv = ["pytest", "-x", "--no-header", "-q"]
    if target:
        argv.append(target)
    result = ws.run(argv, timeout=180)
    output = truncate((result.stdout + "\n" + result.stderr).strip(), MAX_COMMAND_OUTPUT_CHARS, keep_tail=True)
    if result.ok:
        return ToolResult.success(f"PASSED\n{output}")
    return ToolResult.success(f"FAILED (exit {result.returncode})\n{output}")


def _run_command(ws: Workspace, args: dict[str, Any]) -> ToolResult:
    argv = args.get("argv")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return ToolResult.failure("argv must be a list of strings")

    error = _validate_argv(argv)
    if error:
        return ToolResult.failure(error)

    result = ws.run(argv, timeout=120)
    output = truncate((result.stdout + "\n" + result.stderr).strip(), MAX_COMMAND_OUTPUT_CHARS, keep_tail=True)
    status = "OK" if result.ok else f"FAILED (exit {result.returncode})"
    return ToolResult.success(f"{status}\n{output}")


RUN_TESTS = Tool(
    name="run_tests",
    description=(
        "Run the pytest test suite (optionally scoped to a target path/test-id) "
        "and return structured pass/fail output including failure tracebacks."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Optional pytest target, e.g. 'tests/test_models.py::test_foo'",
            }
        },
        "required": [],
    },
    handler=_run_tests,
)

RUN_COMMAND = Tool(
    name="run_command",
    description=(
        "Run an allowlisted validation command: pytest, 'ruff check'/'ruff format', "
        "black, mypy, or a read-only git subcommand (diff, status, log, show, branch). "
        "Pass argv as a list, e.g. [\"ruff\", \"check\", \"src/\"]. Any other command "
        "is rejected."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command and arguments as a list, e.g. [\"mypy\", \"src/\"]",
            }
        },
        "required": ["argv"],
    },
    handler=_run_command,
)
