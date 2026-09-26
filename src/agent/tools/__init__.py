from src.agent.tools.base import Tool, ToolRegistry, ToolResult
from src.agent.tools.exec import RUN_COMMAND, RUN_TESTS
from src.agent.tools.filesystem import CREATE_FILE, EDIT_FILE, LIST_FILES, READ_FILE
from src.agent.tools.git import GIT_DIFF, GIT_STATUS
from src.agent.tools.search import SEARCH_CODE

ALL_TOOLS: list[Tool] = [
    LIST_FILES,
    READ_FILE,
    SEARCH_CODE,
    EDIT_FILE,
    CREATE_FILE,
    RUN_TESTS,
    RUN_COMMAND,
    GIT_DIFF,
    GIT_STATUS,
]


def default_registry() -> ToolRegistry:
    return ToolRegistry(ALL_TOOLS)


__all__ = ["ALL_TOOLS", "Tool", "ToolRegistry", "ToolResult", "default_registry"]
