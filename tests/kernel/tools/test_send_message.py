"""Tests for kernel.agents.mustang.tools.builtin.send_message.SendMessageTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from kernel.agents.mustang.tasks.registry import TaskRegistry
from kernel.agents.mustang.tasks.types import AgentTaskState, TaskStatus
from kernel.agents.mustang.tools.builtin.send_message import SendMessageTool
from kernel.agents.mustang.tools.types import ToolCallResult


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


def _ctx(**overrides: Any) -> MagicMock:
    """Build a minimal ToolContext mock."""
    ctx = MagicMock()
    ctx.session_id = "test-session"
    ctx.agent_id = None
    ctx.tasks = overrides.get("tasks", TaskRegistry())
    ctx.spawn_subagent = overrides.get("spawn_subagent", None)
    ctx.deliver_cross_session = overrides.get("deliver_cross_session", None)
    ctx.route_agent_message = overrides.get("route_agent_message", None)
    return ctx


async def _collect(tool: SendMessageTool, input: dict, ctx: Any) -> ToolCallResult:
    """Run tool.call() and return the final ToolCallResult."""
    result = None
    async for event in tool.call(input, ctx):
        if isinstance(event, ToolCallResult):
            result = event
    assert result is not None
    return result


class TestBroadcastPath:
    @pytest.mark.asyncio
    async def test_broadcast_not_supported(self) -> None:
        tool = SendMessageTool()
        ctx = _ctx()
        result = await _collect(tool, {"to": "*", "message": "hi"}, ctx)
        assert result.data["success"] is False
        assert "not yet supported" in result.data["message"]


class TestCrossSessionPath:
    @pytest.mark.asyncio
    async def test_deliver_success(self) -> None:
        tool = SendMessageTool()
        deliver = MagicMock(return_value=True)
        ctx = _ctx(deliver_cross_session=deliver)

        result = await _collect(
            tool, {"to": "session:abc-123", "message": "hello"}, ctx
        )
        assert result.data["success"] is True
        deliver.assert_called_once_with("abc-123", "hello")

    @pytest.mark.asyncio
    async def test_deliver_target_not_found(self) -> None:
        tool = SendMessageTool()
        deliver = MagicMock(return_value=False)
        ctx = _ctx(deliver_cross_session=deliver)

        result = await _collect(
            tool, {"to": "session:no-such", "message": "hi"}, ctx
        )
        assert result.data["success"] is False
        assert "not found" in result.data["message"]

    @pytest.mark.asyncio
    async def test_empty_session_id(self) -> None:
        tool = SendMessageTool()
        ctx = _ctx(deliver_cross_session=MagicMock())
        result = await _collect(
            tool, {"to": "session:", "message": "hi"}, ctx
        )
        assert result.data["success"] is False
        assert "Empty session ID" in result.data["message"]

    @pytest.mark.asyncio
    async def test_no_deliver_wired(self) -> None:
        tool = SendMessageTool()
        ctx = _ctx(deliver_cross_session=None)
        result = await _collect(
            tool, {"to": "session:abc", "message": "hi"}, ctx
        )
        assert result.data["success"] is False
        assert "not available" in result.data["message"]


class TestDurableAgentPath:
    @pytest.mark.asyncio
    async def test_route_durable_agent_success(self) -> None:
        tool = SendMessageTool()
        route = MagicMock(return_value=True)
        ctx = _ctx(route_agent_message=route)

        result = await _collect(
            tool, {"to": "agent:peer", "message": "hello"}, ctx
        )

        assert result.data["success"] is True
        route.assert_called_once_with("peer", "hello")

    @pytest.mark.asyncio
    async def test_route_durable_agent_target_not_found(self) -> None:
        tool = SendMessageTool()
        ctx = _ctx(route_agent_message=MagicMock(return_value=False))

        result = await _collect(
            tool, {"to": "agent:missing", "message": "hello"}, ctx
        )

        assert result.data["success"] is False
        assert "not found" in result.data["message"]

    @pytest.mark.asyncio
    async def test_route_durable_agent_requires_router_bridge(self) -> None:
        tool = SendMessageTool()
        ctx = _ctx(route_agent_message=None)

        result = await _collect(
            tool, {"to": "agent:peer", "message": "hello"}, ctx
        )

        assert result.data["success"] is False
        assert "Router not wired" in result.data["message"]

    @pytest.mark.asyncio
    async def test_empty_agent_id(self) -> None:
        tool = SendMessageTool()
        ctx = _ctx(route_agent_message=MagicMock())

        result = await _collect(
            tool, {"to": "agent:", "message": "hello"}, ctx
        )

        assert result.data["success"] is False
        assert "Empty agent ID" in result.data["message"]


class TestInSessionAgentPath:
    @pytest.mark.asyncio
    async def test_queue_by_name(self) -> None:
        tool = SendMessageTool()
        reg = TaskRegistry()
        task = _agent("a1", name="explorer")
        reg.register(task)
        reg.register_name("explorer", "a1")
        ctx = _ctx(tasks=reg)

        result = await _collect(
            tool, {"to": "explorer", "message": "check logs"}, ctx
        )
        assert result.data["success"] is True
        assert "queued" in result.data["message"]
        # Message should be in the task's pending queue.
        assert task.pending_messages == ["check logs"]

    @pytest.mark.asyncio
    async def test_queue_by_task_id(self) -> None:
        tool = SendMessageTool()
        reg = TaskRegistry()
        task = _agent("a1")
        reg.register(task)
        ctx = _ctx(tasks=reg)

        result = await _collect(
            tool, {"to": "a1", "message": "hello"}, ctx
        )
        assert result.data["success"] is True
        assert task.pending_messages == ["hello"]

    @pytest.mark.asyncio
    async def test_agent_not_found(self) -> None:
        tool = SendMessageTool()
        ctx = _ctx()
        result = await _collect(
            tool, {"to": "nobody", "message": "hi"}, ctx
        )
        assert result.data["success"] is False
        assert "not found" in result.data["message"]

    @pytest.mark.asyncio
    async def test_non_agent_task(self) -> None:
        """Sending to a shell task should fail."""
        from kernel.agents.mustang.tasks.types import ShellTaskState

        tool = SendMessageTool()
        reg = TaskRegistry()
        shell = ShellTaskState(
            id="b1",
            status=TaskStatus.running,
            description="echo",
            command="echo hello",
        )
        reg.register(shell)
        ctx = _ctx(tasks=reg)

        result = await _collect(
            tool, {"to": "b1", "message": "hi"}, ctx
        )
        assert result.data["success"] is False
        assert "not an agent task" in result.data["message"]

    @pytest.mark.asyncio
    async def test_no_registry(self) -> None:
        tool = SendMessageTool()
        ctx = _ctx(tasks=None)
        ctx.tasks = None
        result = await _collect(
            tool, {"to": "explorer", "message": "hi"}, ctx
        )
        assert result.data["success"] is False
        assert "not available" in result.data["message"]


class TestResumeAgent:
    @pytest.mark.asyncio
    async def test_resume_completed_agent(self) -> None:
        """SendMessage to a completed agent should resume it."""
        tool = SendMessageTool()
        reg = TaskRegistry()
        task = _agent("a1", name="explorer", status=TaskStatus.completed)
        task.transcript = [{"role": "user", "content": "original prompt"}]
        reg.register(task)
        reg.register_name("explorer", "a1")

        # Mock spawn_subagent — we just need it to exist.
        async def mock_spawn(*args: Any, **kwargs: Any):
            # Yield nothing (agent finishes immediately).
            return
            yield  # unreachable — makes this an async generator

        ctx = _ctx(tasks=reg, spawn_subagent=mock_spawn)

        result = await _collect(
            tool, {"to": "explorer", "message": "follow up"}, ctx
        )
        assert result.data["success"] is True
        assert "resumed" in result.data["message"]
        # Name should point to the new task ID (not a1 anymore).
        new_id = reg.resolve_name("explorer")
        assert new_id is not None
        assert new_id != "a1"

    @pytest.mark.asyncio
    async def test_resume_without_spawn(self) -> None:
        tool = SendMessageTool()
        reg = TaskRegistry()
        task = _agent("a1", status=TaskStatus.completed)
        reg.register(task)
        ctx = _ctx(tasks=reg, spawn_subagent=None)

        result = await _collect(
            tool, {"to": "a1", "message": "hi"}, ctx
        )
        assert result.data["success"] is False
        assert "not available" in result.data["message"]


class TestToolMetadata:
    def test_name(self) -> None:
        assert SendMessageTool.name == "SendMessage"

    def test_concurrency_safe(self) -> None:
        assert SendMessageTool.is_concurrency_safe is True

    def test_required_fields(self) -> None:
        assert "to" in SendMessageTool.input_schema["required"]
        assert "message" in SendMessageTool.input_schema["required"]
