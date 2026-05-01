"""Background shell task management.

Manages asynchronously-running shell commands.  The LLM can use
``bash(run_in_background=True)`` to start a command, continue
working, and receive a notification when it completes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

# Max output file size (10 MB).
_MAX_OUTPUT_BYTES = 10 * 1024 * 1024


@dataclass
class ShellTask:
    """Represents a single background shell command."""

    id: str
    command: str
    description: str
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    output_path: Path = field(default_factory=lambda: Path("/dev/null"))
    started_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    exit_code: int | None = None
    cwd: str = ""

    @property
    def elapsed(self) -> float:
        end = self.completed_at or time.monotonic()
        return end - self.started_at


class TaskManager:
    """Manages background shell tasks for a session.

    Output is written to ``session_dir/tasks/{id}.out`` to avoid
    memory buildup.  Completed tasks are queued for notification
    injection into the next orchestrator round.
    """

    def __init__(self, session_dir: Path | None = None) -> None:
        self._tasks: dict[str, ShellTask] = {}
        self._pending_notifications: list[str] = []
        self._async_tasks: dict[str, asyncio.Task[None]] = {}
        self._tasks_dir: Path | None = None
        if session_dir is not None:
            self._tasks_dir = session_dir / "tasks"
            self._tasks_dir.mkdir(parents=True, exist_ok=True)

    async def spawn(
        self,
        command: str,
        cwd: Path | str,
        timeout: int = 600,
        description: str = "",
    ) -> str:
        """Start a background shell command, return task ID."""
        task_id = uuid4().hex[:8]

        output_path = Path("/dev/null")
        if self._tasks_dir is not None:
            output_path = self._tasks_dir / f"{task_id}.out"

        shell_task = ShellTask(
            id=task_id,
            command=command,
            description=description or command[:60],
            output_path=output_path,
            cwd=str(cwd),
        )
        self._tasks[task_id] = shell_task

        async_task = asyncio.create_task(self._run_task(shell_task, timeout))
        self._async_tasks[task_id] = async_task

        logger.info("Background task %s started: %s", task_id, command[:80])
        return task_id

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        task = self._tasks.get(task_id)
        if task is None or task.status != "running":
            return False

        async_task = self._async_tasks.get(task_id)
        if async_task and not async_task.done():
            async_task.cancel()
            try:
                await async_task
            except asyncio.CancelledError:
                pass

        task.status = "cancelled"
        task.completed_at = time.monotonic()
        self._pending_notifications.append(task_id)
        logger.info("Background task %s cancelled", task_id)
        return True

    def collect_notifications(self) -> list[ShellTask]:
        """Return completed tasks since last call, clear queue."""
        result = []
        for task_id in self._pending_notifications:
            task = self._tasks.get(task_id)
            if task is not None:
                result.append(task)
        self._pending_notifications.clear()
        return result

    def get(self, task_id: str) -> ShellTask | None:
        return self._tasks.get(task_id)

    def read_output_tail(self, task_id: str, max_chars: int = 500) -> str:
        """Read the last N characters of a task's output."""
        task = self._tasks.get(task_id)
        if task is None:
            return ""
        try:
            data = task.output_path.read_bytes()
            text = data.decode(errors="replace")
            return text[-max_chars:] if len(text) > max_chars else text
        except (OSError, ValueError):
            return ""

    async def cleanup(self) -> None:
        """Kill all running tasks (session shutdown)."""
        for task_id, async_task in list(self._async_tasks.items()):
            if not async_task.done():
                async_task.cancel()
                try:
                    await async_task
                except asyncio.CancelledError:
                    pass
            task = self._tasks.get(task_id)
            if task and task.status == "running":
                task.status = "cancelled"
                task.completed_at = time.monotonic()
        self._async_tasks.clear()

    async def _run_task(self, task: ShellTask, timeout: int) -> None:
        """Execute the command and write output to disk."""
        try:
            proc = await asyncio.create_subprocess_shell(
                task.command,
                cwd=task.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ},
            )

            # Stream output to file.
            total_bytes = 0
            with open(task.output_path, "wb") as f:
                assert proc.stdout is not None
                try:
                    async with asyncio.timeout(timeout):
                        while True:
                            chunk = await proc.stdout.read(4096)
                            if not chunk:
                                break
                            if total_bytes + len(chunk) > _MAX_OUTPUT_BYTES:
                                remaining = _MAX_OUTPUT_BYTES - total_bytes
                                if remaining > 0:
                                    f.write(chunk[:remaining])
                                f.write(b"\n[output truncated at 10MB]")
                                break
                            f.write(chunk)
                            total_bytes += len(chunk)
                except TimeoutError:
                    proc.kill()
                    f.write(f"\n[timed out after {timeout}s]".encode())

            await proc.wait()
            task.exit_code = proc.returncode
            task.status = "completed" if proc.returncode == 0 else "failed"

        except asyncio.CancelledError:
            task.status = "cancelled"
            raise
        except Exception as exc:
            logger.exception("Background task %s crashed", task.id)
            task.status = "failed"
            task.exit_code = -1
            try:
                task.output_path.write_text(f"Internal error: {exc}")
            except OSError:
                pass
        finally:
            task.completed_at = time.monotonic()
            self._pending_notifications.append(task.id)
            self._async_tasks.pop(task.id, None)
            logger.info(
                "Background task %s finished: status=%s exit=%s elapsed=%.1fs",
                task.id,
                task.status,
                task.exit_code,
                task.elapsed,
            )
