"""TaskStopTool — stop a running background task.

Design reference: ``docs/plans/task-manager.md`` § 4.2.
Claude Code equivalent: ``src/tools/TaskStopTool/TaskStopTool.ts``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tasks.types import AgentTaskState, MonitorTaskState, ShellTaskState, TaskStatus
from kernel.tools.context import ToolContext
from kernel.tools.tool import Tool
from kernel.tools.types import (
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
)


class TaskStopTool(Tool[dict[str, Any], str]):
    """Stop a running background task by ID."""

    name = "TaskStop"
    description_key = "tools/task_stop"
    description = "Stop a background task."
    kind = ToolKind.execute
    aliases = ("KillShell",)

    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the background task to stop.",
            },
        },
        "required": ["task_id"],
    }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        task_id = input["task_id"]

        if ctx.tasks is None:
            yield _error("Task system not available")
            return

        task = ctx.tasks.get(task_id)
        if task is None:
            yield _error(f"No task found with ID: {task_id}")
            return

        if task.status != TaskStatus.running:
            yield _error(f"Task {task_id} is not running (status: {task.status.value})")
            return

        # Kill the process or cancel the agent
        if isinstance(task, (ShellTaskState, MonitorTaskState)) and task.process is not None:
            task.process.kill()
        elif isinstance(task, AgentTaskState) and task.cancel_event is not None:
            task.cancel_event.set()

        ctx.tasks.update_status(task_id, TaskStatus.killed)
        ctx.tasks.enqueue_notification(task_id)

        body = f"Successfully stopped task: {task_id} ({task.description})"
        yield ToolCallResult(
            data={
                "message": body,
                "task_id": task_id,
                "task_type": task.type.value,
            },
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


def _error(msg: str) -> ToolCallResult:
    return ToolCallResult(
        data={"error": msg},
        llm_content=[TextBlock(type="text", text=f"Error: {msg}")],
        display=TextDisplay(text=f"Error: {msg}"),
    )


__all__ = ["TaskStopTool"]
