"""Tests for kernel.tasks.registry."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import (
    AgentTaskState,
    ShellTaskState,
    TaskStatus,
)


def _shell(task_id: str = "b00000001", **kw: object) -> ShellTaskState:
    defaults: dict = dict(
        id=task_id,
        status=TaskStatus.running,
        description="echo hello",
        command="echo hello",
    )
    defaults.update(kw)
    return ShellTaskState(**defaults)


def _agent(task_id: str = "a00000001", **kw: object) -> AgentTaskState:
    defaults: dict = dict(
        id=task_id,
        status=TaskStatus.running,
        description="explore",
        agent_id=task_id,
        agent_type="general-purpose",
        prompt="do stuff",
    )
    defaults.update(kw)
    return AgentTaskState(**defaults)


class TestRegisterAndGet:
    def test_register_sets_start_time(self) -> None:
        reg = TaskRegistry()
        task = _shell()
        assert task.start_time == 0.0
        reg.register(task)
        assert task.start_time > 0

    def test_get_returns_registered(self) -> None:
        reg = TaskRegistry()
        task = _shell()
        reg.register(task)
        assert reg.get("b00000001") is task

    def test_get_missing_returns_none(self) -> None:
        reg = TaskRegistry()
        assert reg.get("nonexistent") is None

    def test_get_all(self) -> None:
        reg = TaskRegistry()
        reg.register(_shell("b1"))
        reg.register(_shell("b2"))
        assert len(reg.get_all()) == 2

    def test_get_running(self) -> None:
        reg = TaskRegistry()
        reg.register(_shell("b1"))
        t2 = _shell("b2", status=TaskStatus.completed)
        reg.register(t2)
        running = reg.get_running()
        assert len(running) == 1
        assert running[0].id == "b1"


class TestUpdateStatus:
    def test_terminal_sets_end_time(self) -> None:
        reg = TaskRegistry()
        task = _shell()
        reg.register(task)
        reg.update_status("b00000001", TaskStatus.completed, exit_code=0)
        assert task.status == TaskStatus.completed
        assert task.end_time is not None
        assert task.exit_code == 0

    def test_shell_exit_code(self) -> None:
        reg = TaskRegistry()
        task = _shell()
        reg.register(task)
        reg.update_status("b00000001", TaskStatus.failed, exit_code=1)
        assert task.exit_code == 1

    def test_agent_result_and_error(self) -> None:
        reg = TaskRegistry()
        task = _agent()
        reg.register(task)
        reg.update_status("a00000001", TaskStatus.completed, result="done")
        assert task.result == "done"

        reg2 = TaskRegistry()
        task2 = _agent("a2")
        reg2.register(task2)
        reg2.update_status("a2", TaskStatus.failed, error="boom")
        assert task2.error == "boom"

    def test_missing_task_returns_none(self) -> None:
        reg = TaskRegistry()
        assert reg.update_status("nope", TaskStatus.completed) is None


class TestNotifications:
    def test_enqueue_and_drain_root(self) -> None:
        reg = TaskRegistry()
        task = _shell(owner_agent_id=None)
        reg.register(task)
        reg.update_status("b00000001", TaskStatus.completed)
        reg.enqueue_notification("b00000001")

        ids = reg.drain_notifications(agent_id=None)
        assert ids == ["b00000001"]
        assert task.notified is True

    def test_drain_filters_by_agent(self) -> None:
        reg = TaskRegistry()
        t1 = _shell("b1", owner_agent_id=None)
        t2 = _shell("b2", owner_agent_id="agent_x")
        reg.register(t1)
        reg.register(t2)
        reg.update_status("b1", TaskStatus.completed)
        reg.update_status("b2", TaskStatus.completed)
        reg.enqueue_notification("b1")
        reg.enqueue_notification("b2")

        # Root agent only gets b1
        root_ids = reg.drain_notifications(agent_id=None)
        assert root_ids == ["b1"]

        # agent_x gets b2
        agent_ids = reg.drain_notifications(agent_id="agent_x")
        assert agent_ids == ["b2"]

    def test_no_double_notification(self) -> None:
        reg = TaskRegistry()
        task = _shell()
        reg.register(task)
        reg.update_status("b00000001", TaskStatus.completed)
        reg.enqueue_notification("b00000001")
        reg.enqueue_notification("b00000001")  # second call should be no-op

        ids = reg.drain_notifications(agent_id=None)
        assert len(ids) == 1

    def test_drain_empty_returns_empty(self) -> None:
        reg = TaskRegistry()
        assert reg.drain_notifications() == []


class TestEvict:
    def test_evicts_notified_terminal(self) -> None:
        reg = TaskRegistry()
        task = _shell()
        reg.register(task)
        reg.update_status("b00000001", TaskStatus.completed)
        task.notified = True

        evicted = reg.evict_terminal()
        assert evicted == ["b00000001"]
        assert reg.get("b00000001") is None

    def test_keeps_running(self) -> None:
        reg = TaskRegistry()
        reg.register(_shell())
        evicted = reg.evict_terminal()
        assert evicted == []
        assert reg.get("b00000001") is not None

    def test_keeps_non_notified_terminal(self) -> None:
        reg = TaskRegistry()
        task = _shell()
        reg.register(task)
        reg.update_status("b00000001", TaskStatus.completed)
        # notified is still False
        evicted = reg.evict_terminal()
        assert evicted == []


class TestShutdown:
    @pytest.mark.asyncio
    async def test_kills_running_shell(self) -> None:
        reg = TaskRegistry()
        mock_proc = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        task = _shell(process=mock_proc)
        reg.register(task)

        await reg.shutdown()

        mock_proc.kill.assert_called_once()
        assert task.status == TaskStatus.killed
        assert reg.get_all() == []

    @pytest.mark.asyncio
    async def test_cancels_running_agent(self) -> None:
        reg = TaskRegistry()
        cancel = asyncio.Event()
        task = _agent(cancel_event=cancel)
        reg.register(task)

        await reg.shutdown()

        assert cancel.is_set()
        assert task.status == TaskStatus.killed

    @pytest.mark.asyncio
    async def test_clears_all(self) -> None:
        reg = TaskRegistry()
        reg.register(_shell("b1"))
        reg.register(_shell("b2", status=TaskStatus.completed))
        await reg.shutdown()
        assert reg.get_all() == []


class TestTodos:
    def test_roundtrip(self) -> None:
        reg = TaskRegistry()
        todos = [{"content": "fix bug", "status": "pending"}]
        reg.set_todos(None, todos)
        assert reg.get_todos(None) == todos

    def test_agent_scoped(self) -> None:
        reg = TaskRegistry()
        reg.set_todos(None, [{"content": "root", "status": "pending"}])
        reg.set_todos("a1", [{"content": "agent", "status": "in_progress"}])
        assert len(reg.get_todos(None)) == 1
        assert reg.get_todos(None)[0]["content"] == "root"
        assert reg.get_todos("a1")[0]["content"] == "agent"

    def test_clear_on_empty(self) -> None:
        reg = TaskRegistry()
        reg.set_todos(None, [{"content": "x", "status": "pending"}])
        reg.set_todos(None, [])
        assert reg.get_todos(None) == []

    def test_get_missing_returns_empty(self) -> None:
        reg = TaskRegistry()
        assert reg.get_todos("nonexistent") == []


class TestNameRegistry:
    def test_register_and_resolve(self) -> None:
        reg = TaskRegistry()
        assert reg.register_name("explorer", "a1")
        assert reg.resolve_name("explorer") == "a1"

    def test_duplicate_name_rejected(self) -> None:
        reg = TaskRegistry()
        reg.register_name("explorer", "a1")
        assert not reg.register_name("explorer", "a2")
        assert reg.resolve_name("explorer") == "a1"

    def test_unregister_name(self) -> None:
        reg = TaskRegistry()
        reg.register_name("explorer", "a1")
        reg.unregister_name("explorer")
        assert reg.resolve_name("explorer") is None

    def test_unregister_idempotent(self) -> None:
        reg = TaskRegistry()
        reg.unregister_name("nonexistent")  # should not raise

    def test_update_name(self) -> None:
        reg = TaskRegistry()
        reg.register_name("explorer", "a1")
        reg.update_name("explorer", "a2")
        assert reg.resolve_name("explorer") == "a2"

    def test_resolve_missing_returns_none(self) -> None:
        reg = TaskRegistry()
        assert reg.resolve_name("nothing") is None

    def test_evict_cleans_name_mapping(self) -> None:
        reg = TaskRegistry()
        task = _agent("a1", name="explorer")
        reg.register(task)
        reg.register_name("explorer", "a1")
        reg.update_status("a1", TaskStatus.completed)
        task.notified = True

        reg.evict_terminal()
        assert reg.resolve_name("explorer") is None


class TestMessageQueue:
    def test_queue_and_drain(self) -> None:
        reg = TaskRegistry()
        task = _agent("a1")
        reg.register(task)
        assert reg.queue_message("a1", "hello")
        assert reg.queue_message("a1", "world")
        msgs = reg.drain_messages("a1")
        assert msgs == ["hello", "world"]
        # Second drain should be empty.
        assert reg.drain_messages("a1") == []

    def test_queue_nonexistent_returns_false(self) -> None:
        reg = TaskRegistry()
        assert not reg.queue_message("nope", "msg")

    def test_queue_shell_task_returns_false(self) -> None:
        reg = TaskRegistry()
        reg.register(_shell("b1"))
        assert not reg.queue_message("b1", "msg")

    def test_drain_nonexistent_returns_empty(self) -> None:
        reg = TaskRegistry()
        assert reg.drain_messages("nope") == []

    def test_drain_empty_returns_empty(self) -> None:
        reg = TaskRegistry()
        reg.register(_agent("a1"))
        assert reg.drain_messages("a1") == []


class TestOnChange:
    def test_listener_fires(self) -> None:
        reg = TaskRegistry()
        calls: list[str] = []
        reg.on_change(lambda: calls.append("fired"))
        reg.register(_shell())
        assert calls == ["fired"]

    def test_unsubscribe(self) -> None:
        reg = TaskRegistry()
        calls: list[str] = []
        unsub = reg.on_change(lambda: calls.append("fired"))
        unsub()
        reg.register(_shell())
        assert calls == []
