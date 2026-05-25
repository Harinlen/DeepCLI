"""Probe Agent Network tool closure through the real tool classes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kernel.agent_hub.manager.agent_network_service import AgentNetworkPolicy, AgentNetworkService
from kernel.agent_hub.manager.runtime_backends import AcpRuntimeController, FakeAcpRuntime
from kernel.agent_hub.manager.spawned_runs import SpawnedRunRegistry
from kernel.agents.mustang.tasks.registry import TaskRegistry
from kernel.agents.mustang.tools.builtin import BUILTIN_TOOLS
from kernel.agents.mustang.tools.builtin.multi_agent import (
    AgentDirectoryTool,
    AgentMessageTool,
    AgentSessionTool,
)
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.file_state import FileStateCache
from kernel.agents.mustang.tools.types import ToolCallResult


class FakeCommands:
    def __init__(self, *, stale: bool = False) -> None:
        self.stale = stale
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
        if self.stale:
            raise RuntimeError("route stale: primary")
        self.sent.append({"agent_id": agent_id, "message": message, "session_id": session_id})
        return {"delivered": True}


def _ctx(service: AgentNetworkService) -> ToolContext:
    return ToolContext(
        session_id="probe-session",
        agent_depth=0,
        agent_id=None,
        cwd=Path.cwd(),
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
        tasks=TaskRegistry(),
        agent_network_request=service.request,
    )


async def _collect(tool: Any, payload: dict[str, Any], ctx: ToolContext) -> ToolCallResult:
    result: ToolCallResult | None = None
    async for event in tool.call(payload, ctx):
        if isinstance(event, ToolCallResult):
            result = event
    if result is None:
        raise AssertionError(f"{tool.name} did not yield a ToolCallResult")
    return result


async def main() -> None:
    builtin_names = {tool.name for tool in BUILTIN_TOOLS}
    deprecated_names = {"agents_list", "sessions_send", "sessions_spawn", "subagents"}
    required_names = {"AgentDirectory", "AgentMessage", "AgentSession"}

    print("probe=agent_network_tools")
    assert required_names.issubset(builtin_names), sorted(required_names - builtin_names)
    assert not deprecated_names.intersection(builtin_names), sorted(deprecated_names & builtin_names)
    print("tool_snapshot=PASS required=AgentDirectory,AgentMessage,AgentSession")
    print("deprecated_aliases_recommended=false")

    service = AgentNetworkService(FakeCommands(), policy=AgentNetworkPolicy(frozenset({"research"})))
    directory = await _collect(AgentDirectoryTool(), {}, _ctx(service))
    assert [agent["agentId"] for agent in directory.data["agents"]] == ["research"]
    print("tool=AgentDirectory case=policy_allow result=PASS visible_agents=1")

    denied_directory = await _collect(
        AgentDirectoryTool(),
        {},
        _ctx(AgentNetworkService(FakeCommands(), policy=AgentNetworkPolicy(frozenset({"missing"})))),
    )
    assert denied_directory.data["agents"] == []
    print("tool=AgentDirectory case=policy_deny result=PASS visible_agents=0")

    commands = FakeCommands()
    sent = await _collect(
        AgentMessageTool(),
        {
            "agentId": "research",
            "message": "hello",
            "wait": True,
            "timeoutSeconds": 5,
            "announce": True,
            "replyBack": True,
        },
        _ctx(AgentNetworkService(commands)),
    )
    assert sent.data["success"] is True
    assert sent.data["route"] == "access_router"
    assert sent.data["accepted"] is True
    assert sent.data["waited"] is True
    assert sent.data["provenance"]["kind"] == "inter_session"
    assert commands.sent
    print(
        "tool=AgentMessage case=agent_id_deliver result=PASS "
        "route=access_router delivered=true wait=true provenance=inter_session"
    )

    stale = await _collect(
        AgentMessageTool(),
        {"agentId": "research", "message": "hello"},
        _ctx(AgentNetworkService(FakeCommands(stale=True))),
    )
    assert stale.data["success"] is False
    assert stale.data["error"] == "route_unavailable"
    print("tool=AgentMessage case=stale_route result=PASS error=route_unavailable")

    denied = await _collect(
        AgentMessageTool(),
        {"agentId": "primary", "message": "hello"},
        _ctx(AgentNetworkService(FakeCommands(), policy=AgentNetworkPolicy(frozenset({"research"})))),
    )
    assert denied.data["success"] is False
    assert denied.data["denied"] is True
    print("tool=AgentMessage case=policy_deny result=PASS denied=true")

    with TemporaryDirectory() as tmp:
        registry = SpawnedRunRegistry.open(Path(tmp))
        try:
            session_ctx = _ctx(AgentNetworkService(FakeCommands(), run_registry=registry))
            spawned = await _collect(
                AgentSessionTool(),
                {"action": "spawn", "runtime": "agent", "agentId": "research", "task": "work"},
                session_ctx,
            )
            run_id = spawned.data["runId"]
            listed = await _collect(
                AgentSessionTool(),
                {"action": "list", "runtime": "agent"},
                session_ctx,
            )
            stopped = await _collect(
                AgentSessionTool(),
                {"action": "stop", "runtime": "agent", "runId": run_id},
                session_ctx,
            )
            assert spawned.data["success"] is True
            assert spawned.data["delivery"]["accepted"] is True
            assert listed.data["runs"][0]["runId"] == run_id
            assert listed.data["runs"][0]["provenance"]["kind"] == "inter_session"
            assert stopped.data["run"]["status"] == "stopped"
        finally:
            registry.close()
    print(f"tool=AgentSession case=runtime_agent_spawn result=PASS run_id={run_id} delivered=true")

    with TemporaryDirectory() as tmp:
        registry = SpawnedRunRegistry.open(Path(tmp))
        try:
            acp_runtime = FakeAcpRuntime()
            acp = await _collect(
                AgentSessionTool(),
                {"action": "spawn", "runtime": "acp", "task": "permission work", "wait": True},
                _ctx(
                    AgentNetworkService(
                        FakeCommands(),
                        run_registry=registry,
                        acp_controller=AcpRuntimeController(acp_runtime),
                    )
                ),
            )
            assert acp.data["success"] is True
            assert acp.data["runtime"] == "acp"
            assert acp_runtime.permission_requests
        finally:
            registry.close()
    print("tool=AgentSession case=runtime_acp result=PASS permission_tunnel=true")

    unsupported_acp = await _collect(
        AgentSessionTool(),
        {"action": "spawn", "runtime": "acp", "task": "work"},
        _ctx(AgentNetworkService(FakeCommands())),
    )
    assert unsupported_acp.data["unsupported"] is True
    print("tool=AgentSession case=runtime_acp_missing_controller result=PASS error=acp_runtime_unavailable")

    local = await _collect(
        AgentSessionTool(),
        {"action": "spawn", "runtime": "local", "task": "work"},
        _ctx(AgentNetworkService(FakeCommands())),
    )
    assert local.data["compatibility"] is True
    print("tool=AgentSession case=runtime_local_compat result=PASS compatibility=true")

    print("agent_bindings=0")
    print("result=PASS")


if __name__ == "__main__":
    asyncio.run(main())
