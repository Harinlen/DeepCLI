"""Tests for task notification drain + orphan drain + GC in orchestrator."""

from kernel.orchestrator.orchestrator import _format_task_notification
from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import AgentTaskState, ShellTaskState, TaskStatus


def _shell(task_id: str = "b00000001", **kw: object) -> ShellTaskState:
    defaults: dict = dict(
        id=task_id, status=TaskStatus.running, description="echo hi", command="echo hi"
    )
    defaults.update(kw)
    return ShellTaskState(**defaults)


def _agent(task_id: str = "a00000001", **kw: object) -> AgentTaskState:
    defaults: dict = dict(
        id=task_id, status=TaskStatus.running, description="explore",
        agent_id=task_id, agent_type="general-purpose", prompt="do stuff",
    )
    defaults.update(kw)
    return AgentTaskState(**defaults)


class TestFormatTaskNotification:
    def test_shell_completed(self) -> None:
        t = _shell(exit_code=0)
        t.status = TaskStatus.completed
        xml = _format_task_notification(t)
        assert "<task-id>b00000001</task-id>" in xml
        assert "<status>completed</status>" in xml
        assert "completed" in xml
        assert "(exit code 0)" in xml

    def test_shell_failed(self) -> None:
        t = _shell(exit_code=1)
        t.status = TaskStatus.failed
        xml = _format_task_notification(t)
        assert "failed" in xml
        assert "exit code 1" in xml

    def test_shell_killed(self) -> None:
        t = _shell()
        t.status = TaskStatus.killed
        xml = _format_task_notification(t)
        assert "was stopped" in xml

    def test_agent_completed_with_result(self) -> None:
        t = _agent(result="done!")
        t.status = TaskStatus.completed
        xml = _format_task_notification(t)
        assert "<result>done!</result>" in xml
        assert 'Agent "explore" completed' in xml

    def test_agent_failed_with_error(self) -> None:
        t = _agent(error="boom")
        t.status = TaskStatus.failed
        xml = _format_task_notification(t)
        assert "boom" in xml

    def test_tool_use_id_included(self) -> None:
        t = _shell(tool_use_id="tu_123")
        t.status = TaskStatus.completed
        xml = _format_task_notification(t)
        assert "<tool-use-id>tu_123</tool-use-id>" in xml


class TestDrainAndOrphan:
    def test_drain_routes_by_agent(self) -> None:
        reg = TaskRegistry()
        t1 = _shell("b1", owner_agent_id=None)
        t2 = _shell("b2", owner_agent_id="agent_x")
        reg.register(t1)
        reg.register(t2)
        reg.update_status("b1", TaskStatus.completed)
        reg.update_status("b2", TaskStatus.completed)
        reg.enqueue_notification("b1")
        reg.enqueue_notification("b2")

        root = reg.drain_notifications(agent_id=None)
        assert root == ["b1"]

        child = reg.drain_notifications(agent_id="agent_x")
        assert child == ["b2"]

    def test_orphan_drain(self) -> None:
        """After sub-agent ends, root takes over its notifications."""
        reg = TaskRegistry()
        t = _shell("b1", owner_agent_id="dead_agent")
        reg.register(t)
        reg.update_status("b1", TaskStatus.completed)
        reg.enqueue_notification("b1")

        # Root can't see it
        assert reg.drain_notifications(agent_id=None) == []

        # Orphan drain: root claims dead agent's notifications
        orphans = reg.drain_notifications(agent_id="dead_agent")
        assert orphans == ["b1"]

    def test_evict_after_drain(self) -> None:
        reg = TaskRegistry()
        t = _shell()
        reg.register(t)
        reg.update_status("b00000001", TaskStatus.completed)
        reg.enqueue_notification("b00000001")
        reg.drain_notifications(agent_id=None)

        evicted = reg.evict_terminal()
        assert "b00000001" in evicted
        assert reg.get("b00000001") is None
