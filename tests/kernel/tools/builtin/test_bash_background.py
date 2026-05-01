"""Tests for BashTool run_in_background + stall watchdog."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import TaskStatus
from kernel.tools.builtin.bash import (
    BashTool,
    _looks_like_prompt,
    _wait_and_notify,
)
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import ToolCallResult


def _make_ctx(tmp_path: Path) -> ToolContext:
    registry = TaskRegistry()
    return ToolContext(
        session_id="test-session",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        tasks=registry,
        queue_reminders=None,
    )


class TestSpawnBackground:
    @pytest.mark.asyncio
    async def test_returns_task_id(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        tool = BashTool()
        results = []
        async for event in tool.call(
            {"command": "echo hello", "run_in_background": True},
            ctx,
        ):
            results.append(event)

        assert len(results) == 1
        result = results[0]
        assert result.data["task_id"].startswith("b")
        assert result.data["status"] == "running"

        # Task should be registered
        task = ctx.tasks.get(result.data["task_id"])
        assert task is not None
        assert task.status == TaskStatus.running

    @pytest.mark.asyncio
    async def test_background_disabled_when_no_registry(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.tasks = None
        tool = BashTool()
        results = []
        async for event in tool.call(
            {"command": "echo hello", "run_in_background": True},
            ctx,
        ):
            results.append(event)

        # Should fall through to foreground execution, possibly streaming progress first.
        terminal = [event for event in results if isinstance(event, ToolCallResult)]
        assert len(terminal) == 1
        assert "hello" in str(terminal[0].data.get("stdout", ""))

    @pytest.mark.asyncio
    async def test_foreground_unchanged(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        tool = BashTool()
        results = []
        async for event in tool.call({"command": "echo foreground"}, ctx):
            results.append(event)

        terminal = [event for event in results if isinstance(event, ToolCallResult)]
        assert len(terminal) == 1
        assert "foreground" in terminal[0].data["stdout"]


class TestWaitAndNotify:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        registry = TaskRegistry()
        from kernel.tasks.types import ShellTaskState

        task = ShellTaskState(
            id="b_test1", status=TaskStatus.running,
            description="test", command="test",
        )
        registry.register(task)

        proc = AsyncMock()
        proc.wait = AsyncMock(return_value=0)
        proc.kill = MagicMock()

        await _wait_and_notify("b_test1", proc, 120_000, registry)

        assert task.status == TaskStatus.completed
        assert task.exit_code == 0

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        registry = TaskRegistry()
        from kernel.tasks.types import ShellTaskState

        task = ShellTaskState(
            id="b_test2", status=TaskStatus.running,
            description="test", command="test",
        )
        registry.register(task)

        proc = AsyncMock()
        proc.wait = AsyncMock(return_value=1)

        await _wait_and_notify("b_test2", proc, 120_000, registry)

        assert task.status == TaskStatus.failed
        assert task.exit_code == 1

    @pytest.mark.asyncio
    async def test_timeout_kills(self) -> None:
        registry = TaskRegistry()
        from kernel.tasks.types import ShellTaskState

        task = ShellTaskState(
            id="b_test3", status=TaskStatus.running,
            description="test", command="test",
        )
        registry.register(task)

        # Simulate a process that hangs: wait() sleeps 60s,
        # but timeout_ms=50 → 0.05s, so wait_for will raise TimeoutError.
        proc = MagicMock()
        proc.kill = MagicMock()

        async def _hang() -> int:
            await asyncio.sleep(60)
            return 0

        # wait() must return a coroutine each time it's called
        # (once by wait_for, once after kill)
        call_count = 0

        async def _wait_impl() -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(60)  # hang on first call
            return -1  # second call (after kill) returns immediately

        proc.wait = _wait_impl

        await _wait_and_notify("b_test3", proc, 50, registry)

        proc.kill.assert_called_once()
        assert task.status == TaskStatus.failed
        assert task.exit_code == -1


class TestLooksLikePrompt:
    @pytest.mark.parametrize(
        "tail",
        [
            "Do you want to continue? (y/n) ",
            "Overwrite file? [Y/n] ",
            "Are you sure? ",
            "Press Enter to continue",
            "Continue?",
            "Overwrite?",
        ],
    )
    def test_detects_prompts(self, tail: str) -> None:
        assert _looks_like_prompt(tail) is True

    @pytest.mark.parametrize(
        "tail",
        [
            "Building project...",
            "100% complete",
            "Compiling main.rs",
            "",
        ],
    )
    def test_rejects_non_prompts(self, tail: str) -> None:
        assert _looks_like_prompt(tail) is False
