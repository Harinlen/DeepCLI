from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from kernel.access_router.gateway_commands import GatewayCommandService
from kernel.access_router.repository import AccessRouterRepository
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeRegisterRequest
from kernel.agent_hub.manager.command_surface import AgentCommandService
from kernel.agent_hub.manager.manager import AgentManager


def test_agent_commands_mutate_definitions_identity_bindings_and_grants(tmp_path: Path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    repo = AccessRouterRepository.open(tmp_path)
    try:
        repo.declare_adapter(adapter_id="test", adapter_type="test", config={}, actor="primary")
        commands = AgentCommandService(manager=manager, gateway_repository=repo)

        created = commands.add("worker", workspace=tmp_path / "worker", name="Worker")
        listed = commands.list(include_bindings=True)
        identity = commands.set_identity(
            "worker",
            name="Worker 2",
            avatar="avatar.png",
            theme="blue",
        )
        binding = commands.bind(agent_id="worker", bind="test:ops", session_id="session-ops")
        bindings = commands.bindings(agent_id="worker")
        removed = commands.unbind(agent_id="worker", bind="test:ops")
        grant = commands.grant("worker", "agent_control", scope="agent")
        grants = commands.grants("worker")
        revoked = commands.revoke_grant(str(grant["grant_id"]))

        assert created["agent_id"] == "worker"
        assert created["runtime"]["autostart"] is True
        assert any(row["agent_id"] == "worker" for row in listed["agents"])
        assert identity["name"] == "Worker 2"
        identity_payload = cast(dict[str, object], identity["identity"])
        assert identity_payload["avatar"] == "avatar.png"
        assert binding["binding_id"] == "test:ops"
        assert bindings[0]["target_agent_id"] == "worker"
        assert removed == 1
        assert grants[0]["grant_id"] == grant["grant_id"]
        assert revoked["revoked_at"] is not None
        assert manager.routing_snapshot().revision >= 3
    finally:
        repo.close()
        manager.close()


def test_agent_command_failures_are_typed(tmp_path: Path) -> None:
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        commands = AgentCommandService(manager=manager)
        commands.add("worker", workspace=tmp_path / "worker")
        with pytest.raises(ValueError, match="already exists"):
            commands.add("worker", workspace=tmp_path / "worker2")
        with pytest.raises(PermissionError, match="confirm"):
            commands.delete("worker", confirm=False)
        created = manager.get("worker")
        assert created is not None
        manager.delete(
            "worker",
            expected_revision=created.revision,
            actor_agent_id="primary",
            confirm=True,
        )
        with pytest.raises(KeyError):
            commands.start("worker", router_endpoint="ws://127.0.0.1:1", router_token="t")
        with pytest.raises(PermissionError, match="primary"):
            commands.grant(
                "worker",
                "agent_control",
                scope="agent",
                actor_agent_id="worker",
            )
    finally:
        manager.close()


@pytest.mark.anyio
async def test_agent_send_routes_one_turn_or_returns_route_unavailable(tmp_path: Path) -> None:
    router = AccessRouter(auth_token="secret", resource_home=tmp_path)
    manager = AgentManager(home=tmp_path)
    manager.startup()
    try:
        async def handler(request: DeliverTurnRequest) -> dict[str, object]:
            return {"text": f"{request.agent_id}:{request.prompt}"}

        router.register_runtime(_register("worker"), handler)
        commands = AgentCommandService(manager=manager, router=router)

        sent = await commands.send(agent_id="worker", message="hello")

        assert sent["text"] == "worker:hello"
        with pytest.raises(Exception, match="route unavailable"):
            await commands.send(agent_id="missing", message="hello")
    finally:
        router.close()
        manager.close()


def test_gateway_commands_manage_status_bindings_and_failures(tmp_path: Path) -> None:
    repo = AccessRouterRepository.open(tmp_path)
    try:
        service = GatewayCommandService(repo)
        repo.declare_adapter(adapter_id="test", adapter_type="test", config={}, actor="primary")

        listed = service.list()
        disabled = service.disable("test")
        enabled = service.enable("test")
        binding = service.bind(gateway_id="test", channel_key="ops", agent_id="primary")
        bindings = service.bindings(gateway_id="test")
        reload_failed = service.reload("test", fail=True)
        service.unbind(str(binding["binding_id"]))

        assert listed[0]["gateway_id"] == "test"
        assert disabled == 2
        assert enabled == 3
        assert binding["gateway_id"] == "test"
        assert bindings[0]["channel_key"] == "ops"
        assert reload_failed.status == "failed"
        assert repo.adapter_event_count("test") >= 4
        assert service.bindings(gateway_id="test") == []
        with pytest.raises(KeyError, match="unknown gateway"):
            service.bind(gateway_id="missing", channel_key="ops", agent_id="primary")
        service.bind(gateway_id="test", channel_key="ops", agent_id="primary")
        with pytest.raises(ValueError, match="already bound"):
            service.bind(gateway_id="test", channel_key="ops", agent_id="primary")
    finally:
        repo.close()


def _register(agent_id: str) -> RuntimeRegisterRequest:
    return RuntimeRegisterRequest(
        process_id=f"runtime-{agent_id}",
        pid=123,
        agent_id=agent_id,
        protocol_version=1,
        capabilities=("session",),
        auth_token="secret",
    )
