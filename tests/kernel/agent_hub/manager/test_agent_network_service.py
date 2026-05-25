from __future__ import annotations

from typing import Any

import pytest

from kernel.agent_hub.manager.agent_network_service import AgentNetworkPolicy, AgentNetworkService
from kernel.agent_hub.manager.runtime_backends import AcpRuntimeController, FakeAcpRuntime
from kernel.agent_hub.manager.spawned_runs import SpawnedRunRegistry


class FakeCommands:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def list(self, *, include_bindings: bool = False) -> dict[str, Any]:
        return {
            "agents": [
                {"agent_id": "primary", "name": "Primary", "workspace": "/tmp/primary"},
                {"agent_id": "research", "name": "Research", "workspace": "/tmp/research"},
            ],
            "bindings": [] if include_bindings else None,
        }

    async def send(
        self,
        *,
        agent_id: str,
        message: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self.sent.append({"agent_id": agent_id, "message": message, "session_id": session_id})
        return {"delivered": True}


def test_agent_network_directory_filters_by_policy() -> None:
    service = AgentNetworkService(
        FakeCommands(),  # type: ignore[arg-type]
        policy=AgentNetworkPolicy(allow_agents=frozenset({"research"})),
    )

    result = service.list_visible_agents()

    assert [agent["agentId"] for agent in result["agents"]] == ["research"]
    assert result["agents"][0]["canSend"] is True


@pytest.mark.asyncio
async def test_agent_network_send_uses_command_surface() -> None:
    commands = FakeCommands()
    service = AgentNetworkService(commands)  # type: ignore[arg-type]

    result = await service.send_message(agent_id="primary", message="hello")

    assert result["success"] is True
    assert result["route"] == "access_router"
    assert commands.sent == [{"agent_id": "primary", "message": "hello", "session_id": None}]


@pytest.mark.asyncio
async def test_agent_network_send_policy_deny() -> None:
    commands = FakeCommands()
    service = AgentNetworkService(
        commands,  # type: ignore[arg-type]
        policy=AgentNetworkPolicy(allow_agents=frozenset({"research"})),
    )

    result = await service.send_message(agent_id="primary", message="hello")

    assert result["success"] is False
    assert result["denied"] is True
    assert commands.sent == []


@pytest.mark.asyncio
async def test_agent_network_session_returns_typed_unsupported() -> None:
    service = AgentNetworkService(FakeCommands())  # type: ignore[arg-type]

    result = await service.session_request({"runtime": "acp"})

    assert result["success"] is False
    assert result["unsupported"] is True


@pytest.mark.asyncio
async def test_agent_network_spawn_message_list_stop(tmp_path) -> None:
    commands = FakeCommands()
    registry = SpawnedRunRegistry.open(tmp_path)
    try:
        service = AgentNetworkService(commands, run_registry=registry)  # type: ignore[arg-type]
        spawned = await service.session_request(
            {
                "action": "spawn",
                "runtime": "agent",
                "agentId": "research",
                "task": "do work",
                "mode": "session",
                "parentSessionId": "parent",
            }
        )

        assert spawned["success"] is True
        run_id = spawned["runId"]
        delivered = await service.send_message(agent_id="", run_id=run_id, message="continue")
        listed = await service.session_request({"action": "list", "runtime": "agent"})
        stopped = await service.session_request({"action": "stop", "runtime": "agent", "runId": run_id})

        assert delivered["success"] is True
        assert listed["runs"][0]["runId"] == run_id
        assert stopped["run"]["status"] == "stopped"
        assert commands.sent[0]["agent_id"] == "research"
        assert commands.sent[0]["session_id"] == spawned["sessionId"]
    finally:
        registry.close()


@pytest.mark.asyncio
async def test_agent_network_acp_session_uses_controller(tmp_path) -> None:
    registry = SpawnedRunRegistry.open(tmp_path)
    runtime = FakeAcpRuntime()
    try:
        service = AgentNetworkService(
            FakeCommands(),  # type: ignore[arg-type]
            run_registry=registry,
            acp_controller=AcpRuntimeController(runtime),
        )

        result = await service.session_request(
            {
                "action": "spawn",
                "runtime": "acp",
                "task": "permission please",
                "parentSessionId": "parent",
                "wait": True,
            }
        )

        assert result["success"] is True
        assert result["runtime"] == "acp"
        assert result["run"]["runtime"] == "acp"
        assert result["run"]["status"] == "completed"
        assert runtime.permission_requests
    finally:
        registry.close()
