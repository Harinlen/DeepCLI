"""CronListTool — list all scheduled cron jobs.

Deferred tool: loaded via ToolSearchTool on demand.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
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


class CronListTool(Tool[dict[str, Any], dict[str, Any]]):
    """List all scheduled cron jobs."""

    name: ClassVar[str] = "CronList"
    description_key: ClassVar[str] = "tools/cron_list"
    description: ClassVar[str] = "List all scheduled cron jobs and their status."
    kind: ClassVar[ToolKind] = ToolKind.read
    should_defer: ClassVar[bool] = True
    search_hint: ClassVar[str] = "cron list show jobs schedule status"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "include_completed": {
                "type": "boolean",
                "description": "Also show completed/expired/deleted jobs.",
                "default": False,
            },
        },
    }

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        from kernel.schedule.schedule_parser import human_schedule

        schedule_mgr = ctx.schedule_manager
        if schedule_mgr is None:
            raise ToolInputError("Schedule subsystem is not enabled.")

        include_completed = input.get("include_completed", False)
        tasks = await schedule_mgr.list_tasks(include_completed=include_completed)

        def _format_time(epoch: float | None) -> str | None:
            if epoch is None:
                return None
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone()
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        jobs = []
        for t in tasks:
            jobs.append(
                {
                    "id": t.id,
                    "schedule": human_schedule(t.schedule),
                    "prompt": t.prompt[:200],
                    "description": t.description,
                    "recurring": t.recurring,
                    "durable": t.durable,
                    "status": t.status.value,
                    "fire_count": t.fire_count,
                    "last_fired_at": _format_time(t.last_fired_at),
                    "next_fire_at": _format_time(t.next_fire_at),
                }
            )

        if not jobs:
            body = "No cron jobs found."
        else:
            lines = []
            for j in jobs:
                line = (
                    f"- [{j['id']}] {j['schedule']} | "
                    f"{j['status']} | "
                    f"fires={j['fire_count']} | "
                    f"next={j['next_fire_at'] or 'N/A'}"
                )
                if j["description"]:
                    line += f" | {j['description']}"
                lines.append(line)
            body = f"{len(jobs)} cron job(s):\n" + "\n".join(lines)

        yield ToolCallResult(
            data={"jobs": jobs},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


__all__ = ["CronListTool"]
