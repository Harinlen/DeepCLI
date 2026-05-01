"""MonitorTool — stream background process output as system reminders.

Design reference: ``docs/plans/schedule-manager.md`` § 4.4.

Unlike ``BashTool run_in_background`` (fire-and-forget, notifies once on
completion), Monitor continuously buffers new output lines into a
``MonitorTaskState.recent_lines`` ring buffer.  The Orchestrator drains
this buffer every turn and injects the lines as a ``<monitor-update>``
system reminder, allowing the LLM to react to real-time output while
continuing other work.

Typical use: ``tail -f``, ``kubectl logs -f``, ``watch``, or any
long-running stream the LLM needs to observe.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tasks.id import generate_task_id
from kernel.tasks.output import TaskOutput
from kernel.tasks.types import MonitorTaskState, TaskStatus, TaskType
from kernel.tools.context import ToolContext
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POLL_INTERVAL_S: float = 0.5
"""How often the reader coroutine checks for new output."""

_DEFAULT_TIMEOUT_MS: int = 300_000
"""Default auto-stop timeout (5 minutes)."""

_MAX_BUFFERED_LINES: int = 50
"""Ring buffer cap — oldest lines are evicted when full."""


# ---------------------------------------------------------------------------
# MonitorTool
# ---------------------------------------------------------------------------


class MonitorTool(Tool[dict[str, Any], dict[str, Any]]):
    """Start a background monitor that streams command output as reminders.

    Each batch of new stdout lines is drained by the Orchestrator every
    turn and injected as a ``<monitor-update>`` system reminder.  The
    tool call itself returns immediately after spawning the process.
    """

    name: ClassVar[str] = "Monitor"
    description_key: ClassVar[str] = "tools/monitor"
    description: ClassVar[str] = "Monitor a running background process."
    kind: ClassVar[ToolKind] = ToolKind.execute
    should_defer: ClassVar[bool] = True
    search_hint: ClassVar[str] = "monitor stream watch background process output events"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Shell command to monitor (e.g. "
                    "'tail -f /var/log/app.log | grep --line-buffered ERROR')"
                ),
            },
            "description": {
                "type": "string",
                "description": "What this monitor watches for",
            },
            "timeout_ms": {
                "type": "integer",
                "description": (
                    "Auto-stop after this many milliseconds "
                    f"(default {_DEFAULT_TIMEOUT_MS}, i.e. 5 minutes)"
                ),
            },
        },
        "required": ["command", "description"],
    }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        """Monitor spawns a shell process — same risk as BashTool."""
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason="Monitor spawns a shell command",
        )

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        command: str = input["command"]
        description: str = input["description"]
        timeout_ms: int = input.get("timeout_ms", _DEFAULT_TIMEOUT_MS)

        if ctx.tasks is None:
            yield _error("Task system not available")
            return

        # Spawn the subprocess with fd-based output (same as BashTool).
        task_id = generate_task_id(TaskType.monitor)
        output = TaskOutput(ctx.session_id, task_id)
        output_path = await output.init_file()

        fd = os.open(output_path, os.O_WRONLY | os.O_APPEND)
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=fd,
            stderr=fd,
            cwd=str(ctx.cwd),
            env={**os.environ, **ctx.env} if ctx.env else None,
        )
        os.close(fd)  # child inherited the fd

        task = MonitorTaskState(
            id=task_id,
            status=TaskStatus.running,
            description=description,
            owner_agent_id=ctx.agent_id,
            command=command,
            output_file=output_path,
            process=process,
            max_buffered_lines=_MAX_BUFFERED_LINES,
        )
        ctx.tasks.register(task)

        # Background reader: polls output file → fills recent_lines buffer.
        asyncio.create_task(_monitor_reader(task_id, output_path, ctx.tasks))
        # Background timeout: kills process after timeout_ms.
        asyncio.create_task(_monitor_timeout(task_id, process, timeout_ms, ctx.tasks))

        body = (
            f"Monitor started for: {description}\n"
            f"Task ID: {task_id}\n"
            f"Command: {command}\n"
            f"Timeout: {timeout_ms // 1000}s\n"
            f"New output will be delivered as system reminders each turn."
        )
        yield ToolCallResult(
            data={"task_id": task_id, "description": description},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


# ---------------------------------------------------------------------------
# Background coroutines (module-level, stateless)
# ---------------------------------------------------------------------------


async def _monitor_reader(
    task_id: str,
    output_path: str,
    registry: Any,
) -> None:
    """Poll the output file and fill ``MonitorTaskState.recent_lines``.

    Runs until the task reaches a terminal status, then does one final
    read to capture any remaining output.  Does NOT push notifications
    itself — the Orchestrator's ``drain_monitor_lines`` handles that at
    each turn boundary.
    """
    offset = 0
    try:
        while True:
            task = registry.get(task_id)
            if task is None:
                return

            # Read new bytes from the output file.
            offset = _read_new_output(output_path, offset, task)

            if task.status.is_terminal:
                # Final read to capture any output written just before exit.
                _read_new_output(output_path, offset, task)
                return

            await asyncio.sleep(_POLL_INTERVAL_S)
    except Exception:
        logger.exception("monitor reader for %s crashed", task_id)


def _read_new_output(output_path: str, offset: int, task: MonitorTaskState) -> int:
    """Read new bytes from the output file into ``task.recent_lines``.

    Returns the updated offset.
    """
    try:
        size = os.path.getsize(output_path)
    except FileNotFoundError:
        return offset

    if size <= offset:
        return offset

    try:
        with open(output_path, "r", errors="replace") as f:
            f.seek(offset)
            chunk = f.read(size - offset)
    except FileNotFoundError:
        return offset

    if chunk:
        lines = chunk.splitlines()
        buf = task.recent_lines
        cap = task.max_buffered_lines
        for line in lines:
            if len(buf) >= cap:
                buf.pop(0)
            buf.append(line)

    return size


async def _monitor_timeout(
    task_id: str,
    process: asyncio.subprocess.Process,
    timeout_ms: int,
    registry: Any,
) -> None:
    """Kill the monitor process after *timeout_ms*."""
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000.0)
    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass

    # Determine exit status.
    returncode = process.returncode
    status = TaskStatus.completed if returncode == 0 else TaskStatus.failed

    task = registry.get(task_id)
    if task is not None and not task.status.is_terminal:
        if isinstance(task, MonitorTaskState) and returncode is not None:
            task.exit_code = returncode
        registry.update_status(task_id, status, exit_code=returncode)
        registry.enqueue_notification(task_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(msg: str) -> ToolCallResult:
    return ToolCallResult(
        data={"error": msg},
        llm_content=[TextBlock(type="text", text=f"Error: {msg}")],
        display=TextDisplay(text=f"Error: {msg}"),
    )


__all__ = ["MonitorTool"]
