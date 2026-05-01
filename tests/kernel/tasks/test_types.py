"""Tests for kernel.tasks.types."""

from kernel.tasks.types import (
    AgentProgress,
    AgentTaskState,
    ShellTaskState,
    TaskStatus,
    TaskType,
)


class TestTaskStatus:
    def test_terminal_statuses(self) -> None:
        assert TaskStatus.completed.is_terminal is True
        assert TaskStatus.failed.is_terminal is True
        assert TaskStatus.killed.is_terminal is True

    def test_non_terminal_statuses(self) -> None:
        assert TaskStatus.pending.is_terminal is False
        assert TaskStatus.running.is_terminal is False

    def test_string_values(self) -> None:
        assert TaskStatus.pending.value == "pending"
        assert TaskStatus.running.value == "running"
        assert TaskStatus.completed.value == "completed"
        assert TaskStatus.failed.value == "failed"
        assert TaskStatus.killed.value == "killed"


class TestTaskType:
    def test_string_values(self) -> None:
        assert TaskType.local_bash.value == "local_bash"
        assert TaskType.local_agent.value == "local_agent"


class TestShellTaskState:
    def test_defaults(self) -> None:
        t = ShellTaskState(
            id="b12345678",
            status=TaskStatus.running,
            description="test cmd",
        )
        assert t.type == TaskType.local_bash
        assert t.command == ""
        assert t.exit_code is None
        assert t.process is None
        assert t.tool_use_id is None
        assert t.owner_agent_id is None
        assert t.output_file == ""
        assert t.notified is False

    def test_mutable_status(self) -> None:
        t = ShellTaskState(
            id="b12345678",
            status=TaskStatus.running,
            description="test",
        )
        t.status = TaskStatus.completed
        assert t.status == TaskStatus.completed


class TestAgentTaskState:
    def test_defaults(self) -> None:
        t = AgentTaskState(
            id="a12345678",
            status=TaskStatus.running,
            description="explore codebase",
        )
        assert t.type == TaskType.local_agent
        assert t.agent_id == ""
        assert t.agent_type == ""
        assert t.prompt == ""
        assert t.model is None
        assert t.result is None
        assert t.error is None
        assert t.progress is None
        assert t.is_backgrounded is False
        assert t.pending_messages == []
        assert t.cancel_event is None

    def test_with_progress(self) -> None:
        p = AgentProgress(tool_use_count=3, token_count=1500, last_activity="Read")
        t = AgentTaskState(
            id="a12345678",
            status=TaskStatus.running,
            description="test",
            progress=p,
        )
        assert t.progress is not None
        assert t.progress.tool_use_count == 3
        assert t.progress.token_count == 1500
        assert t.progress.last_activity == "Read"
