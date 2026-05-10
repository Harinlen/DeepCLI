"""kernel.agents.mustang.tasks — background task framework.

Session-level task registry and output management for background
shell commands (``BashTool run_in_background``) and sub-agents
(``AgentTool run_in_background``).

Design reference: ``docs/plans/task-manager.md``.
"""

from kernel.agents.mustang.tasks.id import generate_task_id
from kernel.agents.mustang.tasks.output import TaskOutput, get_task_output_dir, get_task_output_path
from kernel.agents.mustang.tasks.registry import TaskRegistry
from kernel.agents.mustang.tasks.types import (
    AgentProgress,
    AgentTaskState,
    ShellTaskState,
    TaskState,
    TaskStateBase,
    TaskStatus,
    TaskType,
)

__all__ = [
    "AgentProgress",
    "AgentTaskState",
    "ShellTaskState",
    "TaskOutput",
    "TaskRegistry",
    "TaskState",
    "TaskStateBase",
    "TaskStatus",
    "TaskType",
    "generate_task_id",
    "get_task_output_dir",
    "get_task_output_path",
]
