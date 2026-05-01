"""Built-in tools registered by ``ToolManager.startup``.

Each module exposes one ``Tool`` subclass; ``BUILTIN_TOOLS`` is the
list that ``ToolManager.startup`` iterates over (feature-gated by
``ToolFlags``).

The shell tool (Bash vs PowerShell) is selected at import time based
on platform — see ``kernel.tools.platform.use_powershell_tool``.
"""

from __future__ import annotations

from kernel.tools.builtin.agent import AgentTool
from kernel.tools.builtin.ask_user_question import AskUserQuestionTool
from kernel.tools.builtin.bash import BashTool
from kernel.tools.builtin.cron_create import CronCreateTool
from kernel.tools.builtin.cron_delete import CronDeleteTool
from kernel.tools.builtin.cron_list import CronListTool
from kernel.tools.builtin.enter_plan_mode import EnterPlanModeTool
from kernel.tools.builtin.exit_plan_mode import ExitPlanModeTool
from kernel.tools.builtin.file_edit import FileEditTool
from kernel.tools.builtin.file_read import FileReadTool
from kernel.tools.builtin.file_write import FileWriteTool
from kernel.tools.builtin.glob_tool import GlobTool
from kernel.tools.builtin.grep_tool import GrepTool
from kernel.tools.builtin.list_mcp_resources import ListMcpResourcesTool
from kernel.tools.builtin.monitor import MonitorTool
from kernel.tools.builtin.python_tool import PythonTool
from kernel.tools.builtin.read_mcp_resource import ReadMcpResourceTool
from kernel.tools.builtin.send_message import SendMessageTool
from kernel.tools.builtin.skill_tool import SkillTool
from kernel.tools.builtin.task_output import TaskOutputTool
from kernel.tools.builtin.task_stop import TaskStopTool
from kernel.tools.builtin.todo_write import TodoWriteTool
from kernel.tools.builtin.web_fetch import WebFetchTool
from kernel.tools.builtin.web_search import WebSearchTool
from kernel.tools.platform import selected_shell_tool
from kernel.tools.tool import Tool


def _shell_tool() -> type[Tool]:
    """Return the platform-appropriate shell tool class.

    On Windows, returns ``PowerShellTool`` or ``CmdTool`` depending on
    availability; on Unix, returns ``BashTool``.
    """
    selected = selected_shell_tool()
    if selected == "PowerShell":
        from kernel.tools.builtin.powershell import PowerShellTool

        return PowerShellTool
    if selected == "Cmd":
        from kernel.tools.builtin.cmd import CmdTool

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
    SkillTool,
    AgentTool,
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
    "SendMessageTool",
    "SkillTool",
    "TaskOutputTool",
    "TaskStopTool",
    "TodoWriteTool",
    "WebFetchTool",
    "WebSearchTool",
]
