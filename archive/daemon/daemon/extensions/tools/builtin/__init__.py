"""Built-in tools — shipped with Mustang.

To add a new built-in tool:
  1. Create a ``Tool`` subclass in this package.
  2. Import it below and add it to ``get_builtin_tools()``.

The ``ExtensionManager`` never imports individual tool classes —
it calls ``get_builtin_tools()`` to get the full set.
"""

from __future__ import annotations

from daemon.config.schema import RuntimeConfig
from daemon.extensions.tools.base import Tool
from daemon.extensions.tools.builtin.agent_tool import AgentTool
from daemon.extensions.tools.builtin.ask_user_question import AskUserQuestionTool
from daemon.extensions.tools.builtin.bash import BashTool
from daemon.extensions.tools.builtin.browser import BrowserTool
from daemon.extensions.tools.builtin.config_tool import ConfigTool
from daemon.extensions.tools.builtin.file_edit import FileEditTool
from daemon.extensions.tools.builtin.file_read import FileReadTool
from daemon.extensions.tools.builtin.file_write import FileWriteTool
from daemon.extensions.tools.builtin.enter_plan_mode import EnterPlanModeTool
from daemon.extensions.tools.builtin.exit_plan_mode import ExitPlanModeTool
from daemon.extensions.tools.builtin.glob_tool import GlobTool
from daemon.extensions.tools.builtin.grep_tool import GrepTool
from daemon.extensions.tools.builtin.http_fetch import HttpFetchTool
from daemon.extensions.tools.builtin.memory_append import MemoryAppendTool
from daemon.extensions.tools.builtin.memory_delete import MemoryDeleteTool
from daemon.extensions.tools.builtin.memory_list import MemoryListTool
from daemon.extensions.tools.builtin.memory_write import MemoryWriteTool
from daemon.extensions.tools.builtin.page_fetch import PageFetchTool
from daemon.extensions.tools.builtin.todo_write import TodoWriteTool
from daemon.extensions.tools.builtin.web_search import WebSearchTool

__all__ = [
    "AgentTool",
    "BashTool",
    "BrowserTool",
    "ConfigTool",
    "EnterPlanModeTool",
    "ExitPlanModeTool",
    "FileEditTool",
    "FileReadTool",
    "FileWriteTool",
    "GlobTool",
    "GrepTool",
    "HttpFetchTool",
    "MemoryAppendTool",
    "MemoryDeleteTool",
    "MemoryListTool",
    "MemoryWriteTool",
    "PageFetchTool",
    "TodoWriteTool",
    "WebSearchTool",
    "get_builtin_tools",
]


def get_builtin_tools(config: RuntimeConfig) -> list[Tool]:
    """Instantiate all built-in tools with the given config.

    Centralises tool creation so the extension manager does not need
    to know about individual tool classes or their constructor
    signatures.

    Args:
        config: Resolved runtime config (used for tool-specific
            settings like bash timeout).

    Returns:
        List of ready-to-register tool instances.
    """
    tools: list[Tool] = [
        AskUserQuestionTool(),
        BashTool(default_timeout_ms=config.tools.bash.timeout),
        # BrowserTool(),  # disabled — not needed for now
        ConfigTool(config),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        GlobTool(),
        GrepTool(),
        HttpFetchTool(),
        TodoWriteTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        MemoryWriteTool(),
        MemoryAppendTool(),
        MemoryDeleteTool(),
        MemoryListTool(),
        # PageFetchTool(),  # disabled — not needed for now
        WebSearchTool(preferred=config.tools.web_search_backend),
    ]

    # Register agent tool if sub-agent depth allows it.
    if config.agent.max_depth > 0:
        tools.append(AgentTool())

    return tools
