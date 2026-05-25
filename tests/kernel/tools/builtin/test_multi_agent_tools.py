from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kernel.agents.mustang.tasks.registry import TaskRegistry
from kernel.agents.mustang.tasks.types import AgentTaskState, TaskStatus
from kernel.agents.mustang.tools.builtin.multi_agent import (
    AgentDirectoryTool,
    AgentMessageTool,
    AgentSessionTool,
    AgentsListTool,
    SessionsSendTool,
    SubagentsTool,
)
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.file_state import FileStateCache
from kernel.agents.mustang.tools.types import ToolCallResult


def _ctx(tmp_path: Path, **overrides: object) -> ToolContext:
    return ToolContext(
        session_id="test-session",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        tasks=overrides.get("tasks", TaskRegistry()),  # type: ignore[arg-type]
        module_table=overrides.get("module_table"),
        route_agent_message=overrides.get("route_agent_message"),  # type: ignore[arg-type]
        deliver_cross_session=overrides.get("deliver_cross_session"),  # type: ignore[arg-type]
        agent_network_request=overrides.get("agent_network_request"),  # type: ignore[arg-type]
    )


async def _collect(tool: object, input: dict[str, object], ctx: ToolContext) -> ToolCallResult:
    result = None
    async for event in tool.call(input, ctx):  # type: ignore[attr-defined]
        if isinstance(event, ToolCallResult):
            result = event
    assert result is not None
    return result


@pytest.mark.asyncio
async def test_agents_list_reads_agent_hub_manager(tmp_path: Path) -> None:
    async def request(action: str, payload: dict[str, object]) -> dict[str, object]:
        assert action == "directory"
        return {"available": True, "agents": [{"agentId": "primary", "name": "Primary", "canSend": True}]}

    ctx = _ctx(tmp_path, agent_network_request=request)

    result = await _collect(AgentDirectoryTool(), {}, ctx)

    assert result.data["available"] is True
    assert result.data["agents"][0]["agentId"] == "primary"


@pytest.mark.asyncio
async def test_sessions_send_routes_durable_agent(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def request(action: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((action, payload))
        return {"success": True, "route": "access_router"}

    ctx = _ctx(tmp_path, agent_network_request=request)

    result = await _collect(
        AgentMessageTool(),
        {"agentId": "research", "message": "hello"},
        ctx,
    )

    assert result.data["success"] is True
    assert calls == [
        (
            "message",
            {
                "agentId": "research",
                "message": "hello",
                "wait": False,
                "timeoutSeconds": None,
                "announce": False,
                "replyBack": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_agent_session_runtime_acp_is_typed_unsupported(tmp_path: Path) -> None:
    async def request(action: str, payload: dict[str, object]) -> dict[str, object]:
        assert action == "session"
        return {"success": False, "runtime": payload["runtime"], "unsupported": True, "error": "unsupported"}

    ctx = _ctx(tmp_path, agent_network_request=request)

    result = await _collect(
        AgentSessionTool(),
        {"runtime": "acp", "action": "spawn", "task": "go"},
        ctx,
    )

    assert result.data["success"] is False
    assert result.data["unsupported"] is True


@pytest.mark.asyncio
async def test_deprecated_alias_classes_use_agent_network(tmp_path: Path) -> None:
    async def request(action: str, payload: dict[str, object]) -> dict[str, object]:
        if action == "directory":
            return {"available": True, "agents": [{"agentId": "primary"}]}
        return {"success": True, "route": "access_router"}

    ctx = _ctx(tmp_path, agent_network_request=request)

    listed = await _collect(AgentsListTool(), {}, ctx)
    sent = await _collect(
        SessionsSendTool(),
        {"target_agent_id": "primary", "message": "hello"},
        ctx,
    )

    assert listed.data["agents"][0]["agentId"] == "primary"
    assert sent.data["success"] is True


@pytest.mark.asyncio
async def test_subagents_lists_and_queues_messages(tmp_path: Path) -> None:
    registry = TaskRegistry()
    task = AgentTaskState(
        id="a1",
        status=TaskStatus.running,
        description="research",
        agent_id="a1",
        agent_type="general-purpose",
        prompt="go",
        name="researcher",
    )
    registry.register(task)
    registry.register_name("researcher", "a1")
    ctx = _ctx(tmp_path, tasks=registry)

    listed = await _collect(SubagentsTool(), {"action": "list"}, ctx)
    assert listed.data["agents"][0]["id"] == "a1"

    sent = await _collect(
        SubagentsTool(),
        {"action": "send", "agent": "researcher", "message": "status?"},
        ctx,
    )
    assert sent.data["success"] is True
    assert task.pending_messages == ["status?"]
