"""Tests for TaskOutputTool."""

import asyncio
import os
from pathlib import Path

import pytest

from kernel.tasks.output import get_task_output_path
from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import ShellTaskState, TaskStatus
from kernel.tools.builtin.task_output import TaskOutputTool
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


class TestTaskOutputTool:
    @pytest.mark.asyncio
    async def test_completed_task_output(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        task = ShellTaskState(
            id="b_test1", status=TaskStatus.completed,
            description="echo hi", command="echo hi", exit_code=0,
        )
        ctx.tasks.register(task)  # type: ignore[union-attr]

        # Write output file
        path = get_task_output_path("test", "b_test1")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("hello world\n")
        task.output_file = path

        tool = TaskOutputTool()
        results = []
        async for event in tool.call({"task_id": "b_test1"}, ctx):
            results.append(event)

        assert len(results) == 1
        assert results[0].data["retrieval_status"] == "success"
        assert results[0].data["task"]["exit_code"] == 0
        assert "hello world" in results[0].data["task"]["output"]

    @pytest.mark.asyncio
    async def test_missing_task(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        tool = TaskOutputTool()
        results = []
        async for event in tool.call({"task_id": "nonexistent"}, ctx):
            results.append(event)

        assert "error" in results[0].data

    @pytest.mark.asyncio
    async def test_no_registry(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        ctx.tasks = None
        tool = TaskOutputTool()
        results = []
        async for event in tool.call({"task_id": "b1"}, ctx):
            results.append(event)

        assert "error" in results[0].data
