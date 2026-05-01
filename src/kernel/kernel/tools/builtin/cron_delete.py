"""CronDeleteTool — delete a scheduled cron job.

Deferred tool: loaded via ToolSearchTool on demand.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import Tool
from kernel.tools.types import (
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)


class CronDeleteTool(Tool[dict[str, Any], dict[str, Any]]):
    """Delete a scheduled cron job."""

    name: ClassVar[str] = "CronDelete"
    description_key: ClassVar[str] = "tools/cron_delete"
    description: ClassVar[str] = "Cancel a scheduled cron job by ID."
    kind: ClassVar[ToolKind] = ToolKind.execute
    should_defer: ClassVar[bool] = True
    search_hint: ClassVar[str] = "cron delete remove cancel stop job"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Job ID returned by CronCreate.",
            },
        },
        "required": ["id"],
    }

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        schedule_mgr = ctx.schedule_manager
        if schedule_mgr is None:
            raise ToolInputError("Schedule subsystem is not enabled.")

        task_id: str = input["id"]
        ok = await schedule_mgr.delete_task(task_id)

        result = {"id": task_id, "deleted": ok}
        body = f"Deleted cron job {task_id}." if ok else f"Cron job {task_id} not found."
        yield ToolCallResult(
            data=result,
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


__all__ = ["CronDeleteTool"]
