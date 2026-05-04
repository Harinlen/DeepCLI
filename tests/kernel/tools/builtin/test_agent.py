"""Tests for AgentTool."""

import asyncio
import inspect
from pathlib import Path

import pytest

from kernel.orchestrator.events import SubAgentEnd, TextDelta
from kernel.orchestrator.types import StopReason
from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import TaskStatus
from kernel.tools.builtin.agent import AgentTool, _run_agent_background
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import ToolCallProgress, ToolCallResult


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


async def _fake_spawn(prompt, attachments, **kwargs):
    """Mock spawn_subagent that yields a few TextDelta events."""
    yield TextDelta(content="Hello ")
    yield TextDelta(content="world")


async def _fake_spawn_with_stats(prompt, attachments, **kwargs):
    """Mock spawn_subagent that finishes with child usage stats."""
    yield TextDelta(content="stats")
    yield SubAgentEnd(
        agent_id="a_stats",
        stop_reason=StopReason.end_turn,
        input_tokens=1200,
        output_tokens=345,
        tool_use_count=2,
        duration_ms=13000,
    )


async def _failing_spawn(prompt, attachments, **kwargs):
    """Mock spawn_subagent that raises."""
    yield TextDelta(content="partial")
    raise RuntimeError("agent crashed")


class TestAgentToolForeground:
    @pytest.mark.asyncio
    async def test_foreground_collects_text(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        ctx.spawn_subagent = _fake_spawn

        tool = AgentTool()
        results = []
        async for event in tool.call(
            {"description": "test", "prompt": "do stuff"}, ctx
        ):
            results.append(event)

        # Claude Code parity: foreground sub-agent output is returned as the
        # Agent tool result, not streamed into the parent assistant transcript.
        assert not any(isinstance(event, ToolCallProgress) for event in results)
        final = results[-1]
        assert isinstance(final, ToolCallResult)
        assert final.data["result"] == "Hello world"

    @pytest.mark.asyncio
    async def test_foreground_returns_agent_stats_meta(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        ctx.spawn_subagent = _fake_spawn_with_stats

        tool = AgentTool()
        results = []
        async for event in tool.call(
            {"description": "test", "prompt": "do stuff"}, ctx
        ):
            results.append(event)

        final = results[-1]
        assert isinstance(final, ToolCallResult)
        stats = final.data["agent"]
        assert stats == {
            "toolUseCount": 2,
            "inputTokens": 1200,
            "outputTokens": 345,
            "totalTokens": 1545,
            "durationMs": 13000,
        }
        assert final.meta == {"mustang.agent/agentStats": stats}

    @pytest.mark.asyncio
    async def test_foreground_no_spawn(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        ctx.spawn_subagent = None

        tool = AgentTool()
        results = []
        async for event in tool.call(
            {"description": "test", "prompt": "do stuff"}, ctx
        ):
            results.append(event)

        assert "error" in results[0].data


class TestAgentToolBackground:
    @pytest.mark.asyncio
    async def test_background_returns_task_id(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        ctx.spawn_subagent = _fake_spawn

        tool = AgentTool()
        results = []
        async for event in tool.call(
            {
                "description": "bg test",
                "prompt": "do stuff",
                "run_in_background": True,
            },
            ctx,
        ):
            results.append(event)

        assert len(results) == 1
        assert results[0].data["task_id"].startswith("a")
        assert results[0].data["status"] == "running"

    @pytest.mark.asyncio
    async def test_background_no_registry(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        ctx.tasks = None
        ctx.spawn_subagent = _fake_spawn

        tool = AgentTool()
        results = []
        async for event in tool.call(
            {
                "description": "test",
                "prompt": "do stuff",
                "run_in_background": True,
            },
            ctx,
        ):
            results.append(event)

        # Falls through to foreground mode
        final = results[-1]
        assert final.data["result"] == "Hello world"

    def test_agent_tool_has_no_agent_hub_dependency(self) -> None:
        """AgentTool children remain parent-private, not durable Hub agents."""
        source = inspect.getsource(AgentTool) + inspect.getsource(_run_agent_background)

        assert "agent_hub" not in source
        assert "AgentDefinition" not in source
        assert "AgentRuntimeRecord" not in source


class TestRunAgentBackground:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        registry = TaskRegistry()
        from kernel.tasks.types import AgentTaskState

        task = AgentTaskState(
            id="a_bg1", status=TaskStatus.running,
            description="test", agent_id="a_bg1",
            agent_type="general-purpose", prompt="do stuff",
        )
        registry.register(task)

        await _run_agent_background(
            "a_bg1", "do stuff",
            spawn_fn=_fake_spawn, registry=registry,
        )

        assert task.status == TaskStatus.completed
        assert task.result == "Hello world"

    @pytest.mark.asyncio
    async def test_spawn_none(self) -> None:
        registry = TaskRegistry()
        from kernel.tasks.types import AgentTaskState

        task = AgentTaskState(
            id="a_bg2", status=TaskStatus.running,
            description="test", agent_id="a_bg2",
            agent_type="general-purpose", prompt="do stuff",
        )
        registry.register(task)

        await _run_agent_background(
            "a_bg2", "do stuff",
            spawn_fn=None, registry=registry,
        )

        assert task.status == TaskStatus.failed
        assert task.error == "spawn_subagent not available"

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        registry = TaskRegistry()
        from kernel.tasks.types import AgentTaskState

        task = AgentTaskState(
            id="a_bg3", status=TaskStatus.running,
            description="test", agent_id="a_bg3",
            agent_type="general-purpose", prompt="do stuff",
        )
        registry.register(task)

        await _run_agent_background(
            "a_bg3", "do stuff",
            spawn_fn=_failing_spawn, registry=registry,
        )

        assert task.status == TaskStatus.failed
        assert "agent crashed" in (task.error or "")
