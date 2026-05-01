"""Tests for MonitorTool + MonitorTaskState + drain_monitor_lines."""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.tasks.id import generate_task_id
from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import (
    MonitorTaskState,
    ShellTaskState,
    TaskStatus,
    TaskType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _monitor(task_id: str = "m00000001", **kw: object) -> MonitorTaskState:
    defaults: dict = dict(
        id=task_id,
        status=TaskStatus.running,
        description="tail -f app.log",
        command="tail -f /var/log/app.log",
    )
    defaults.update(kw)
    return MonitorTaskState(**defaults)


# ---------------------------------------------------------------------------
# MonitorTaskState data model
# ---------------------------------------------------------------------------


class TestMonitorTaskState:
    def test_type_is_monitor(self) -> None:
        task = _monitor()
        assert task.type == TaskType.monitor

    def test_recent_lines_default_empty(self) -> None:
        task = _monitor()
        assert task.recent_lines == []

    def test_max_buffered_lines_default(self) -> None:
        task = _monitor()
        assert task.max_buffered_lines == 50


class TestTaskIdGeneration:
    def test_monitor_prefix(self) -> None:
        tid = generate_task_id(TaskType.monitor)
        assert tid.startswith("m")
        assert len(tid) == 9  # 1 prefix + 8 suffix


# ---------------------------------------------------------------------------
# TaskRegistry: monitor-specific behavior
# ---------------------------------------------------------------------------


class TestRegistryMonitorSupport:
    def test_register_and_get(self) -> None:
        reg = TaskRegistry()
        task = _monitor()
        reg.register(task)
        assert reg.get("m00000001") is task

    def test_update_status_sets_exit_code(self) -> None:
        reg = TaskRegistry()
        task = _monitor()
        reg.register(task)
        reg.update_status("m00000001", TaskStatus.completed, exit_code=0)
        assert task.exit_code == 0
        assert task.status == TaskStatus.completed
        assert task.end_time is not None

    @pytest.mark.asyncio
    async def test_shutdown_kills_monitor_process(self) -> None:
        reg = TaskRegistry()
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        task = _monitor(process=mock_proc)
        reg.register(task)

        await reg.shutdown()

        mock_proc.kill.assert_called_once()
        assert task.status == TaskStatus.killed
        assert task.process is None


# ---------------------------------------------------------------------------
# drain_monitor_lines
# ---------------------------------------------------------------------------


class TestDrainMonitorLines:
    def test_drain_returns_lines_and_clears(self) -> None:
        reg = TaskRegistry()
        task = _monitor(owner_agent_id=None)
        task.recent_lines = ["line1", "line2"]
        reg.register(task)

        result = reg.drain_monitor_lines(agent_id=None)
        assert result == {"m00000001": ["line1", "line2"]}
        # Buffer should be cleared after drain.
        assert task.recent_lines == []

    def test_drain_empty_buffer_returns_empty(self) -> None:
        reg = TaskRegistry()
        task = _monitor(owner_agent_id=None)
        reg.register(task)

        result = reg.drain_monitor_lines(agent_id=None)
        assert result == {}

    def test_drain_filters_by_agent(self) -> None:
        reg = TaskRegistry()
        t1 = _monitor("m1", owner_agent_id=None)
        t1.recent_lines = ["root line"]
        t2 = _monitor("m2", owner_agent_id="agent_x")
        t2.recent_lines = ["agent line"]
        reg.register(t1)
        reg.register(t2)

        root_result = reg.drain_monitor_lines(agent_id=None)
        assert "m1" in root_result
        assert "m2" not in root_result

        agent_result = reg.drain_monitor_lines(agent_id="agent_x")
        assert "m2" in agent_result

    def test_drain_ignores_non_monitor_tasks(self) -> None:
        reg = TaskRegistry()
        shell = ShellTaskState(
            id="b00000001",
            status=TaskStatus.running,
            description="echo hi",
            command="echo hi",
        )
        reg.register(shell)

        result = reg.drain_monitor_lines(agent_id=None)
        assert result == {}

    def test_ring_buffer_evicts_oldest(self) -> None:
        """Verify that the reader pattern of capping lines works."""
        task = _monitor(max_buffered_lines=3)
        buf = task.recent_lines
        cap = task.max_buffered_lines
        for i in range(5):
            if len(buf) >= cap:
                buf.pop(0)
            buf.append(f"line{i}")

        assert task.recent_lines == ["line2", "line3", "line4"]


# ---------------------------------------------------------------------------
# MonitorTool.call() — integration with real subprocess
# ---------------------------------------------------------------------------


class TestMonitorToolCall:
    """Test MonitorTool.call() with a real short-lived command."""

    @pytest.mark.asyncio
    async def test_spawn_and_read_output(self) -> None:
        """Monitor a command that emits 3 lines, verify buffer fills."""
        from kernel.tools.builtin.monitor import MonitorTool
        from kernel.tools.types import ToolCallResult

        reg = TaskRegistry()

        # Build a minimal ToolContext mock.
        ctx = MagicMock()
        ctx.session_id = "test-session"
        ctx.agent_id = None
        ctx.cwd = os.getcwd()
        ctx.env = {}
        ctx.tasks = reg
        ctx.queue_reminders = None
        ctx.cancel_event = asyncio.Event()

        # Use a temp dir for task output files.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch get_task_output_dir to use tmpdir.
            import kernel.tasks.output as output_mod

            original_fn = output_mod.get_task_output_dir
            output_mod.get_task_output_dir = lambda sid: _ensure_dir(tmpdir)

            try:
                tool = MonitorTool()
                result = None
                async for item in tool.call(
                    {
                        "command": 'for i in 1 2 3; do echo "line$i"; sleep 0.1; done',
                        "description": "test echo",
                        "timeout_ms": 10_000,
                    },
                    ctx,
                ):
                    if isinstance(item, ToolCallResult):
                        result = item

                assert result is not None
                assert "task_id" in result.data
                task_id = result.data["task_id"]
                assert task_id.startswith("m")

                # Task should be registered.
                task = reg.get(task_id)
                assert task is not None
                assert isinstance(task, MonitorTaskState)
                assert task.status == TaskStatus.running

                # Wait for command to finish + reader to pick up output.
                await asyncio.sleep(2.0)

                # Buffer should have lines (reader coroutine fills it).
                assert len(task.recent_lines) > 0

                # drain_monitor_lines should return and clear.
                drained = reg.drain_monitor_lines(agent_id=None)
                assert task_id in drained
                assert task.recent_lines == []

            finally:
                output_mod.get_task_output_dir = original_fn
                await reg.shutdown()

    @pytest.mark.asyncio
    async def test_error_when_no_task_system(self) -> None:
        """MonitorTool returns error when task system is unavailable."""
        from kernel.tools.builtin.monitor import MonitorTool
        from kernel.tools.types import ToolCallResult

        ctx = MagicMock()
        ctx.tasks = None

        tool = MonitorTool()
        result = None
        async for item in tool.call(
            {"command": "echo hi", "description": "test"},
            ctx,
        ):
            if isinstance(item, ToolCallResult):
                result = item

        assert result is not None
        assert "error" in result.data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402


def _ensure_dir(d: str) -> Path:
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p
