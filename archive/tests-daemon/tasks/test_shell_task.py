"""Tests for Phase 5.5.4I — Background shell tasks (TaskManager)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from daemon.tasks.shell_task import ShellTask, TaskManager


class TestTaskManager:
    @pytest.mark.asyncio
    async def test_spawn_and_complete(self, tmp_path: Path) -> None:
        """Spawn a quick command, wait for completion, collect notification."""
        mgr = TaskManager(session_dir=tmp_path)
        task_id = await mgr.spawn("echo hello", cwd=tmp_path, timeout=10)
        assert task_id
        assert mgr.get(task_id) is not None
        assert mgr.get(task_id).status == "running"

        # Wait for completion.
        await asyncio.sleep(0.5)
        completed = mgr.collect_notifications()
        assert len(completed) == 1
        assert completed[0].id == task_id
        assert completed[0].status == "completed"
        assert completed[0].exit_code == 0

    @pytest.mark.asyncio
    async def test_output_captured(self, tmp_path: Path) -> None:
        """Output is written to disk and readable."""
        mgr = TaskManager(session_dir=tmp_path)
        task_id = await mgr.spawn("echo hello_world", cwd=tmp_path, timeout=10)
        await asyncio.sleep(0.5)

        output = mgr.read_output_tail(task_id, 500)
        assert "hello_world" in output

    @pytest.mark.asyncio
    async def test_failed_command(self, tmp_path: Path) -> None:
        """Non-zero exit code → status='failed'."""
        mgr = TaskManager(session_dir=tmp_path)
        task_id = await mgr.spawn("exit 42", cwd=tmp_path, timeout=10)
        await asyncio.sleep(0.5)

        completed = mgr.collect_notifications()
        assert len(completed) == 1
        assert completed[0].status == "failed"
        assert completed[0].exit_code == 42

    @pytest.mark.asyncio
    async def test_cancel(self, tmp_path: Path) -> None:
        """Cancel a running task."""
        mgr = TaskManager(session_dir=tmp_path)
        task_id = await mgr.spawn("sleep 60", cwd=tmp_path, timeout=120)
        await asyncio.sleep(0.1)

        assert await mgr.cancel(task_id)
        task = mgr.get(task_id)
        assert task is not None
        assert task.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, tmp_path: Path) -> None:
        mgr = TaskManager(session_dir=tmp_path)
        assert not await mgr.cancel("nonexistent")

    @pytest.mark.asyncio
    async def test_multiple_tasks(self, tmp_path: Path) -> None:
        """Multiple concurrent tasks tracked independently."""
        mgr = TaskManager(session_dir=tmp_path)
        id1 = await mgr.spawn("echo one", cwd=tmp_path, timeout=10)
        id2 = await mgr.spawn("echo two", cwd=tmp_path, timeout=10)
        await asyncio.sleep(0.5)

        completed = mgr.collect_notifications()
        ids = {c.id for c in completed}
        assert id1 in ids
        assert id2 in ids

    @pytest.mark.asyncio
    async def test_collect_drains_queue(self, tmp_path: Path) -> None:
        """Second collect returns empty after drain."""
        mgr = TaskManager(session_dir=tmp_path)
        await mgr.spawn("echo x", cwd=tmp_path, timeout=10)
        await asyncio.sleep(0.5)

        first = mgr.collect_notifications()
        assert len(first) == 1
        second = mgr.collect_notifications()
        assert len(second) == 0

    @pytest.mark.asyncio
    async def test_cleanup(self, tmp_path: Path) -> None:
        """Cleanup cancels running tasks."""
        mgr = TaskManager(session_dir=tmp_path)
        task_id = await mgr.spawn("sleep 60", cwd=tmp_path, timeout=120)
        await asyncio.sleep(0.1)
        await mgr.cleanup()

        task = mgr.get(task_id)
        assert task is not None
        assert task.status == "cancelled"

    @pytest.mark.asyncio
    async def test_elapsed_time(self, tmp_path: Path) -> None:
        mgr = TaskManager(session_dir=tmp_path)
        task_id = await mgr.spawn("sleep 0.2 && echo done", cwd=tmp_path, timeout=10)
        await asyncio.sleep(0.5)

        task = mgr.get(task_id)
        assert task is not None
        assert task.elapsed >= 0.1

    @pytest.mark.asyncio
    async def test_no_session_dir(self) -> None:
        """TaskManager works without session_dir (output goes to /dev/null)."""
        mgr = TaskManager(session_dir=None)
        task_id = await mgr.spawn("echo test", cwd="/tmp", timeout=10)
        await asyncio.sleep(0.5)

        completed = mgr.collect_notifications()
        assert len(completed) == 1
        assert completed[0].status == "completed"


class TestBashBackgroundIntegration:
    """Test BashTool run_in_background parameter."""

    @pytest.mark.asyncio
    async def test_run_in_background_no_manager(self) -> None:
        """Returns error when task_manager is not available."""
        from daemon.extensions.tools.base import ToolContext
        from daemon.extensions.tools.builtin.bash import BashTool

        tool = BashTool()
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute(
            {"command": "echo hi", "run_in_background": True}, ctx
        )
        assert result.is_error
        assert "not available" in result.output

    @pytest.mark.asyncio
    async def test_run_in_background_with_manager(self, tmp_path: Path) -> None:
        """Returns task ID when manager is available."""
        from daemon.extensions.tools.base import ToolContext
        from daemon.extensions.tools.builtin.bash import BashTool

        mgr = TaskManager(session_dir=tmp_path)
        tool = BashTool()
        ctx = ToolContext(cwd=str(tmp_path), task_manager=mgr)
        result = await tool.execute(
            {"command": "echo hi", "run_in_background": True}, ctx
        )
        assert not result.is_error
        assert "Background task" in result.output
        assert "started" in result.output

        # Wait for the task to complete.
        await asyncio.sleep(0.5)
        completed = mgr.collect_notifications()
        assert len(completed) == 1
        await mgr.cleanup()
