"""CronCreateTool — create a scheduled cron job.

Deferred tool: loaded via ToolSearchTool on demand.
Design reference: ``docs/plans/schedule-manager.md`` § 4.1.
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


class CronCreateTool(Tool[dict[str, Any], dict[str, Any]]):
    """Create a scheduled cron job that runs a prompt on a timer."""

    name: ClassVar[str] = "CronCreate"
    description_key: ClassVar[str] = "tools/cron_create"
    description: ClassVar[str] = "Schedule a prompt to run at a future time."
    kind: ClassVar[ToolKind] = ToolKind.execute
    should_defer: ClassVar[bool] = True
    search_hint: ClassVar[str] = "cron schedule timer loop recurring periodic job task interval"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "schedule": {
                "type": "string",
                "description": (
                    "Schedule expression. Formats:\n"
                    "- Cron: '*/30 * * * *' (every 30 min), "
                    "'0 9 * * 1-5' (weekdays 9am)\n"
                    "- Interval: 'every 30m', 'every 2h', 'every 1d'\n"
                    "- One-shot delay: '5m', '2h' (from now)\n"
                    "- Timestamp: '2026-04-21T09:00' (ISO 8601, local time)"
                ),
            },
            "prompt": {
                "type": "string",
                "description": (
                    "The prompt to execute at each fire time. Should be "
                    "self-contained — it runs in an isolated session with "
                    "no prior context."
                ),
            },
            "description": {
                "type": "string",
                "description": "Human-readable description of what this job does.",
                "default": "",
            },
            "recurring": {
                "type": ["boolean", "null"],
                "description": (
                    "Whether to repeat (true) or fire once (false). "
                    "If null, auto-inferred: cron/interval → true, "
                    "delay/timestamp → false."
                ),
                "default": None,
            },
            "durable": {
                "type": "boolean",
                "description": ("Persist across kernel restarts (true) or session-only (false)."),
                "default": True,
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Skills to load before execution.",
                "default": [],
            },
            "model": {
                "type": ["string", "null"],
                "description": "Model override (e.g. 'claude-sonnet-4-6').",
                "default": None,
            },
            "delivery": {
                "type": "string",
                "description": (
                    "Where to deliver results (comma-separated): "
                    "'session', 'acp', 'gateway:<adapter>:<channel>', "
                    "or 'none'."
                ),
                "default": "session,acp",
            },
            "repeat_count": {
                "type": ["integer", "null"],
                "description": "Run at most N times then stop (null = unlimited).",
                "default": None,
            },
            "repeat_duration": {
                "type": ["string", "null"],
                "description": (
                    "Keep repeating for this duration then stop "
                    "(e.g. '7d', '12h', '30m'). Null = unlimited."
                ),
                "default": None,
            },
            "repeat_until": {
                "type": ["string", "null"],
                "description": (
                    "Stop repeating after this time (ISO 8601 timestamp). Null = unlimited."
                ),
                "default": None,
            },
        },
        "required": ["schedule", "prompt"],
    }

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        """Create a cron task via ScheduleManager."""
        schedule_mgr = ctx.schedule_manager
        if schedule_mgr is None:
            raise ToolInputError("Schedule subsystem is not enabled.")

        schedule_expr: str = input["schedule"]
        prompt: str = input["prompt"]

        # Parse repeat_duration to seconds if provided
        repeat_duration_s: float | None = None
        if input.get("repeat_duration"):
            from kernel.schedule.schedule_parser import parse_schedule

            try:
                dur_sched = parse_schedule(input["repeat_duration"])
                # The parser converts delay strings to at; we need the
                # original interval_seconds or delta from now.
                if dur_sched.kind.value == "every":
                    repeat_duration_s = dur_sched.interval_seconds
                else:
                    # For "7d" → at kind, extract run_at - now
                    import time

                    repeat_duration_s = dur_sched.run_at - time.time()
            except ValueError:
                raise ToolInputError(
                    f"Invalid repeat_duration: {input['repeat_duration']!r}"
                )

        # Parse repeat_until to epoch if provided
        repeat_until_epoch: float | None = None
        if input.get("repeat_until"):
            from kernel.schedule.schedule_parser import _parse_iso

            try:
                dt = _parse_iso(input["repeat_until"])
                repeat_until_epoch = dt.timestamp()
            except ValueError:
                raise ToolInputError(f"Invalid repeat_until: {input['repeat_until']!r}")

        try:
            task = await schedule_mgr.create_task(
                schedule_expr=schedule_expr,
                prompt=prompt,
                description=input.get("description", ""),
                recurring=input.get("recurring"),
                durable=input.get("durable", True),
                skills=input.get("skills", []),
                model=input.get("model"),
                delivery=input.get("delivery", "session,acp"),
                repeat_count=input.get("repeat_count"),
                repeat_duration_seconds=repeat_duration_s,
                repeat_until=repeat_until_epoch,
                session_id=ctx.session_id,
                project_dir=str(ctx.cwd) if ctx.cwd else None,
            )
        except ValueError as exc:
            raise ToolInputError(str(exc))

        from kernel.schedule.schedule_parser import human_schedule
        from datetime import datetime, timezone

        hs = human_schedule(task.schedule)
        next_at = ""
        if task.next_fire_at:
            dt = datetime.fromtimestamp(task.next_fire_at, tz=timezone.utc).astimezone()
            next_at = dt.strftime("%Y-%m-%d %H:%M:%S")

        result = {
            "id": task.id,
            "human_schedule": hs,
            "next_fire_at": next_at,
            "recurring": task.recurring,
            "durable": task.durable,
        }
        body = (
            f"Created cron job {task.id}: {hs}"
            f" ({'recurring' if task.recurring else 'one-shot'})"
            f"\nNext fire: {next_at}"
        )
        yield ToolCallResult(
            data=result,
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


__all__ = ["CronCreateTool"]
