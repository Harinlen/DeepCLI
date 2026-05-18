from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kernel.agent_hub import AgentHub, AgentHubManager
from kernel.agent_hub.contracts import default_primary_agent_definition
from kernel.agents.mustang.tasks.registry import TaskRegistry
from kernel.agents.mustang.tasks.types import AgentTaskState, TaskStatus
from kernel.agents.mustang.tools.builtin.multi_agent import (
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
    hub = AgentHub(
        manager=AgentHubManager(
            [default_primary_agent_definition(home=tmp_path, workspace=str(tmp_path))]
        )
    )
    ctx = _ctx(tmp_path, module_table=SimpleNamespace(agent_hub=hub))

    result = await _collect(AgentsListTool(), {}, ctx)

    assert result.data["available"] is True
    assert result.data["agents"][0]["id"] == "primary"


@pytest.mark.asyncio
async def test_sessions_send_routes_durable_agent(tmp_path: Path) -> None:
    route = MagicMock(return_value=True)
    ctx = _ctx(tmp_path, route_agent_message=route)

    result = await _collect(
        SessionsSendTool(),
        {"target_agent_id": "research", "message": "hello"},
        ctx,
    )

    assert result.data["success"] is True
    route.assert_called_once_with("research", "hello")


@pytest.mark.asyncio
async def test_sessions_send_delivers_cross_session(tmp_path: Path) -> None:
    deliver = MagicMock(return_value=True)
    ctx = _ctx(tmp_path, deliver_cross_session=deliver)

    result = await _collect(
        SessionsSendTool(),
        {"target_session_id": "session-1", "message": "hello"},
        ctx,
    )

    assert result.data["success"] is True
    deliver.assert_called_once_with("session-1", "hello")


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
