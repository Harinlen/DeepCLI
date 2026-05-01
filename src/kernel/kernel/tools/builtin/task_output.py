"""TaskOutputTool — read output from a background task.

Design reference: ``docs/plans/task-manager.md`` § 4.1.
Claude Code equivalent: ``src/tools/TaskOutputTool/TaskOutputTool.tsx``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tasks.output import TaskOutput
from kernel.tasks.types import AgentTaskState, ShellTaskState, TaskStatus
from kernel.tools.context import ToolContext
from kernel.tools.tool import Tool
from kernel.tools.types import (
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
)


class TaskOutputTool(Tool[dict[str, Any], str]):
    """Read output from a background task."""

    name = "TaskOutput"
    description_key = "tools/task_output"
    description = "Read output from a background task."
    kind = ToolKind.read
    aliases = ("BashOutput", "AgentOutput")

    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to get output from.",
            },
            "block": {
                "type": "boolean",
                "description": "Whether to wait for completion. Default true.",
                "default": True,
            },
            "timeout": {
                "type": "number",
                "description": "Max wait time in ms. Default 30000.",
                "default": 30000,
                "minimum": 0,
                "maximum": 600000,
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
        block = input.get("block", True)
        timeout_ms = input.get("timeout", 30000)

        if ctx.tasks is None:
            yield _error("Task system not available")
            return

        task = ctx.tasks.get(task_id)
        if task is None:
            yield _error(f"No task found with ID: {task_id}")
            return

        # Wait for completion if requested
        if block and task.status == TaskStatus.running:
            deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0
            while asyncio.get_event_loop().time() < deadline:
                if ctx.cancel_event.is_set():
                    break
                task = ctx.tasks.get(task_id)
                if task is None or task.status.is_terminal:
                    break
                await asyncio.sleep(0.1)
            # Re-fetch after wait
            task = ctx.tasks.get(task_id)
            if task is None:
                yield _error(f"Task {task_id} disappeared during wait")
                return

        # Read output
        output = TaskOutput(ctx.session_id, task_id)
        content = await output.read_tail()

        result: dict[str, Any] = {
            "task_id": task.id,
            "task_type": task.type.value,
            "status": task.status.value,
            "description": task.description,
            "output": content,
        }
        if isinstance(task, ShellTaskState):
            result["exit_code"] = task.exit_code
        if isinstance(task, AgentTaskState):
            result["prompt"] = task.prompt
            if task.result:
                result["result"] = task.result
            if task.error:
                result["error"] = task.error

        retrieval = "success" if task.status.is_terminal else "timeout"
        body = f"[{retrieval}] Task {task_id} ({task.status.value}):\n{content}"

        yield ToolCallResult(
            data={"retrieval_status": retrieval, "task": result},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


def _error(msg: str) -> ToolCallResult:
    return ToolCallResult(
        data={"error": msg},
        llm_content=[TextBlock(type="text", text=f"Error: {msg}")],
        display=TextDisplay(text=f"Error: {msg}"),
    )


__all__ = ["TaskOutputTool"]
