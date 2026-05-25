"""Built-in tools registered by ``ToolManager.startup``.

Each module exposes one ``Tool`` subclass; ``BUILTIN_TOOLS`` is the
list that ``ToolManager.startup`` iterates over (feature-gated by
``ToolFlags``).

The shell tool (Bash vs PowerShell) is selected at import time based
on platform — see ``kernel.agents.mustang.tools.platform.use_powershell_tool``.
"""

from __future__ import annotations

from kernel.agents.mustang.tools.builtin.agent import AgentTool
from kernel.agents.mustang.tools.builtin.ask_user_question import AskUserQuestionTool
from kernel.agents.mustang.tools.builtin.bash import BashTool
from kernel.agents.mustang.tools.builtin.cron_create import CronCreateTool
from kernel.agents.mustang.tools.builtin.cron_delete import CronDeleteTool
from kernel.agents.mustang.tools.builtin.cron_list import CronListTool
from kernel.agents.mustang.tools.builtin.enter_plan_mode import EnterPlanModeTool
from kernel.agents.mustang.tools.builtin.exit_plan_mode import ExitPlanModeTool
from kernel.agents.mustang.tools.builtin.file_edit import FileEditTool
from kernel.agents.mustang.tools.builtin.file_read import FileReadTool
from kernel.agents.mustang.tools.builtin.file_write import FileWriteTool
from kernel.agents.mustang.tools.builtin.glob_tool import GlobTool
from kernel.agents.mustang.tools.builtin.grep_tool import GrepTool
from kernel.agents.mustang.tools.builtin.list_mcp_resources import ListMcpResourcesTool
from kernel.agents.mustang.tools.builtin.monitor import MonitorTool
from kernel.agents.mustang.tools.builtin.multi_agent import (
    AgentDirectoryTool,
    AgentMessageTool,
    AgentSessionTool,
)
from kernel.agents.mustang.tools.builtin.python_tool import PythonTool
from kernel.agents.mustang.tools.builtin.read_mcp_resource import ReadMcpResourceTool
from kernel.agents.mustang.tools.builtin.restart_self import RestartSelfTool
from kernel.agents.mustang.tools.builtin.send_message import SendMessageTool
from kernel.agents.mustang.tools.builtin.skill_tool import SkillTool
from kernel.agents.mustang.tools.builtin.task_output import TaskOutputTool
from kernel.agents.mustang.tools.builtin.task_stop import TaskStopTool
from kernel.agents.mustang.tools.builtin.todo_write import TodoWriteTool
from kernel.agents.mustang.tools.builtin.web_fetch import WebFetchTool
from kernel.agents.mustang.tools.builtin.web_search import WebSearchTool
from kernel.agents.mustang.tools.platform import selected_shell_tool
from kernel.agents.mustang.tools.tool import Tool


def _shell_tool() -> type[Tool]:
    """Return the platform-appropriate shell tool class.

    On Windows, returns ``PowerShellTool`` or ``CmdTool`` depending on
    availability; on Unix, returns ``BashTool``.
    """
    selected = selected_shell_tool()
    if selected == "PowerShell":
        from kernel.agents.mustang.tools.builtin.powershell import PowerShellTool

        return PowerShellTool
    if selected == "Cmd":
        from kernel.agents.mustang.tools.builtin.cmd import CmdTool

        return CmdTool
    return BashTool


BUILTIN_TOOLS: list[type[Tool]] = [
    _shell_tool(),
    AskUserQuestionTool,
    EnterPlanModeTool,
    ExitPlanModeTool,
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    GlobTool,
    GrepTool,
    ListMcpResourcesTool,
    MonitorTool,
    PythonTool,
    ReadMcpResourceTool,
    RestartSelfTool,
    SkillTool,
    AgentTool,
    AgentDirectoryTool,
    AgentMessageTool,
    AgentSessionTool,
    SendMessageTool,
    TaskOutputTool,
    TaskStopTool,
    TodoWriteTool,
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    WebFetchTool,
    WebSearchTool,
]

__all__ = [
    "AgentTool",
    "AgentDirectoryTool",
    "AgentMessageTool",
    "AgentSessionTool",
    "AskUserQuestionTool",
    "BUILTIN_TOOLS",
    "BashTool",
    "CronCreateTool",
    "CronDeleteTool",
    "CronListTool",
    "EnterPlanModeTool",
    "ExitPlanModeTool",
    "FileEditTool",
    "FileReadTool",
    "FileWriteTool",
    "GlobTool",
    "GrepTool",
    "ListMcpResourcesTool",
    "MonitorTool",
    "PythonTool",
    "ReadMcpResourceTool",
    "RestartSelfTool",
    "SendMessageTool",
    "SkillTool",
    "TaskOutputTool",
    "TaskStopTool",
    "TodoWriteTool",
    "WebFetchTool",
    "WebSearchTool",
]
