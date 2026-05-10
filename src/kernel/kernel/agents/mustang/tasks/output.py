"""Task output collection — file-based.

Design reference: ``docs/plans/task-manager.md`` § 1.4.
Claude Code equivalent: ``src/utils/task/TaskOutput.ts``,
``src/utils/task/diskOutput.ts``.

Bash background tasks write stdout/stderr directly to a file via
subprocess fd (zero Python memory pressure).  This module handles
reading that file for ``TaskOutputTool`` and ``_stall_watchdog``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


def get_task_output_dir(session_id: str) -> Path:
    """Per-session task output directory."""
    base = Path(tempfile.gettempdir()) / "mustang" / session_id / "tasks"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_task_output_path(session_id: str, task_id: str) -> str:
    """Output file path for a specific task."""
    return str(get_task_output_dir(session_id) / f"{task_id}.output")


class TaskOutput:
    """Read-side interface for task output files.

    **File mode** (bash): subprocess stdio fds point directly at the
    output file — data never enters Python.  Progress is obtained by
    polling the file tail.

    Pipe mode (hooks, agent transcript) is not implemented yet.
    """

    def __init__(self, session_id: str, task_id: str) -> None:
        self.session_id = session_id
        self.task_id = task_id
        self.path = get_task_output_path(session_id, task_id)

    async def init_file(self) -> str:
        """Create an empty output file.  Returns the path.

        Uses ``O_CREAT | O_EXCL`` to fail if the path already exists
        (guards against symlink attacks from sandboxed subprocesses).
        """
        await asyncio.to_thread(self._init_file_sync)
        return self.path

    def _init_file_sync(self) -> None:
        # Ensure parent dir exists
        get_task_output_dir(self.session_id)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)

    async def read_all(self, max_bytes: int = 8 * 1024 * 1024) -> str:
        """Read full output, capped at *max_bytes*."""
        try:
            return await asyncio.to_thread(self._read_sync, max_bytes)
        except FileNotFoundError:
            return ""

    def _read_sync(self, max_bytes: int) -> str:
        with open(self.path, "r", errors="replace") as f:
            return f.read(max_bytes)

    async def read_tail(self, max_bytes: int = 8 * 1024 * 1024) -> str:
        """Read the tail of the output file.

        For files <= *max_bytes*, returns everything.  For larger files,
        skips the head and prepends a notice.
        """
        try:
            size = os.path.getsize(self.path)
        except FileNotFoundError:
            return ""
        if size <= max_bytes:
            return await self.read_all(max_bytes)
        offset = size - max_bytes
        data = await asyncio.to_thread(self._read_range_sync, offset, max_bytes)
        skipped_kb = offset // 1024
        return f"[{skipped_kb}KB of earlier output omitted]\n{data}"

    async def read_delta(
        self, from_offset: int, max_bytes: int = 8 * 1024 * 1024
    ) -> tuple[str, int]:
        """Incremental read from *from_offset*.

        Returns ``(content, new_offset)``.
        """
        try:
            data = await asyncio.to_thread(self._read_range_sync, from_offset, max_bytes)
            return data, from_offset + len(data.encode("utf-8"))
        except FileNotFoundError:
            return "", from_offset

    def _read_range_sync(self, offset: int, length: int) -> str:
        with open(self.path, "r", errors="replace") as f:
            f.seek(offset)
            return f.read(length)

    async def cleanup(self) -> None:
        """Delete the output file (fire-and-forget safe)."""
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


__all__ = [
    "TaskOutput",
    "get_task_output_dir",
    "get_task_output_path",
]
