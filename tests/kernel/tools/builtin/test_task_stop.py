"""Tests for TaskStopTool."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import ShellTaskState, TaskStatus
from kernel.tools.builtin.task_stop import TaskStopTool
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="test",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        tasks=TaskRegistry(),
    )


class TestTaskStopTool:
    @pytest.mark.asyncio
    async def test_stop_running_shell(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        mock_proc = MagicMock()
        mock_proc.kill = MagicMock()
        task = ShellTaskState(
            id="b_stop1", status=TaskStatus.running,
            description="sleep", command="sleep 999",
            process=mock_proc,
        )
        ctx.tasks.register(task)  # type: ignore[union-attr]

        tool = TaskStopTool()
        results = []
        async for event in tool.call({"task_id": "b_stop1"}, ctx):
            results.append(event)

        mock_proc.kill.assert_called_once()
        assert task.status == TaskStatus.killed
        assert "Successfully stopped" in results[0].data["message"]

    @pytest.mark.asyncio
    async def test_stop_nonexistent(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        tool = TaskStopTool()
        results = []
        async for event in tool.call({"task_id": "nope"}, ctx):
            results.append(event)

        assert "error" in results[0].data

    @pytest.mark.asyncio
    async def test_stop_already_completed(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        task = ShellTaskState(
            id="b_done", status=TaskStatus.completed,
            description="done", command="echo done",
        )
        ctx.tasks.register(task)  # type: ignore[union-attr]

        tool = TaskStopTool()
        results = []
        async for event in tool.call({"task_id": "b_done"}, ctx):
            results.append(event)

        assert "not running" in results[0].data["error"]
