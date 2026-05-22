from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kernel.access_router.control_api import AccessRouterControlAPI
from kernel.access_router.gateway_commands import GatewayCommandService
from kernel.access_router.repository import AccessRouterRepository
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeRegisterRequest
from kernel.agent_hub.manager.command_surface import AgentCommandService
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agents.access.security.context import AuthContext
from kernel.core.protocol.acp.codec import AcpCodec
from kernel.core.protocol.acp.namespaces import MustangMethod
from kernel.core.protocol.acp.routing import REQUEST_DISPATCH
from kernel.core.protocol.acp.session_handler import AcpSessionHandler


def _auth(connection_id: str) -> AuthContext:
    return AuthContext(
        connection_id=connection_id,
        credential_type="token",
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )


class _ModuleTable:
    def __init__(
        self,
        *,
        agents: AgentCommandService,
        gateways: GatewayCommandService,
    ) -> None:
        self.agent_command_service = agents
        self.gateway_command_service = gateways


async def _request(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    method: str,
    params: dict[str, Any],
    *,
    request_id: int,
) -> dict[str, Any]:
    auth = _auth(f"agent-gateway-management-{request_id}")
    init = codec.decode(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "test", "title": "Test"},
                },
            }
        )
    )
    async for _ in dispatcher.dispatch(init, auth):
        pass
    msg = codec.decode(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    )
    frames = [json.loads(codec.encode(frame)) async for frame in dispatcher.dispatch(msg, auth)]
    return frames[-1]


@pytest.fixture
def management_stack(tmp_path: Path):
    manager = AgentManager(home=tmp_path / "resource")
    manager.startup()
    repo = AccessRouterRepository.open(tmp_path / "resource")
    router = AccessRouter(auth_token="secret")
    agents = AgentCommandService(manager=manager, gateway_repository=repo, router=router)
    gateways = GatewayCommandService(repo)
    dispatcher = AcpSessionHandler(_ModuleTable(agents=agents, gateways=gateways))
    try:
        yield manager, repo, router, dispatcher
    finally:
        router.close()
        repo.close()
        manager.close()


def test_agent_and_gateway_methods_are_routable() -> None:
    for method in (
        MustangMethod.AGENTS_LIST,
        MustangMethod.AGENTS_ADD,
        MustangMethod.AGENTS_DELETE,
        MustangMethod.AGENTS_SET_IDENTITY,
        MustangMethod.AGENTS_BINDINGS,
        MustangMethod.AGENTS_BIND,
        MustangMethod.AGENTS_UNBIND,
        MustangMethod.AGENTS_START,
        MustangMethod.AGENTS_STOP,
        MustangMethod.AGENTS_RESTART,
        MustangMethod.AGENTS_HEALTH,
        MustangMethod.AGENTS_GRANTS,
        MustangMethod.AGENTS_GRANT,
        MustangMethod.AGENTS_REVOKE_GRANT,
        MustangMethod.AGENT_SEND,
        MustangMethod.GATEWAYS_LIST,
        MustangMethod.GATEWAYS_CREATE,
        MustangMethod.GATEWAYS_STATUS,
        MustangMethod.GATEWAYS_DELETE,
        MustangMethod.GATEWAYS_ENABLE,
        MustangMethod.GATEWAYS_DISABLE,
        MustangMethod.GATEWAYS_RELOAD,
        MustangMethod.GATEWAYS_BINDINGS,
        MustangMethod.GATEWAYS_BIND,
        MustangMethod.GATEWAYS_UNBIND,
    ):
        assert method in REQUEST_DISPATCH


@pytest.mark.anyio
async def test_acp_agent_management_conflicts_and_guards(management_stack, tmp_path: Path) -> None:
    manager, _repo, _router, dispatcher = management_stack
    codec = AcpCodec()

    added = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_ADD,
        {"agentId": "worker", "workspace": str(tmp_path / "workspace")},
        request_id=1,
    )
    assert added["result"]["agent"]["agentId"] == "worker"

    duplicate = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_ADD,
        {"agentId": "worker", "workspace": str(tmp_path / "workspace")},
        request_id=2,
    )
    assert duplicate["error"]["code"] == -32602

    delete_without_confirm = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_DELETE,
        {"agentId": "worker", "confirm": False},
        request_id=3,
    )
    assert delete_without_confirm["error"]["code"] == -32602

    ordinary_grant = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_GRANT,
        {
            "actorAgentId": "ordinary",
            "agentId": "worker",
            "capability": "global_resource_write",
        },
        request_id=4,
    )
    assert ordinary_grant["error"]["code"] == -32602

    grant = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_GRANT,
        {
            "agentId": "worker",
            "capability": "global_resource_write",
        },
        request_id=5,
    )
    assert grant["result"]["grant"]["subjectAgentId"] == "worker"

    deleted = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_DELETE,
        {"agentId": "worker", "confirm": True},
        request_id=6,
    )
    assert deleted["result"]["deleted"] is True

    start_deleted = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_START,
        {"agentId": "worker", "routerEndpoint": "ws://127.0.0.1:1", "routerToken": "secret"},
        request_id=7,
    )
    assert start_deleted["error"]["code"] == -32602
    assert manager.get("worker").deleted_at is not None


@pytest.mark.anyio
async def test_agent_send_uses_access_router_and_returns_typed_unavailable(
    management_stack,
) -> None:
    _manager, _repo, router, dispatcher = management_stack
    codec = AcpCodec()
    seen: list[DeliverTurnRequest] = []

    async def handler(request: DeliverTurnRequest) -> dict[str, object]:
        seen.append(request)
        return {"reply": f"from-{request.agent_id}"}

    router.register_runtime(_register(agent_id="worker"), handler)

    delivered = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENT_SEND,
        {"agentId": "worker", "message": "hello"},
        request_id=1,
    )
    assert delivered["result"]["delivered"] is True
    assert delivered["result"]["result"] == {"reply": "from-worker"}
    assert seen[0].prompt == "hello"
    assert router.agent_hub_forward_count == 0

    unavailable = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENT_SEND,
        {"agentId": "ghost", "message": "hello"},
        request_id=2,
    )
    assert unavailable["result"]["delivered"] is False
    assert unavailable["result"]["errorCode"] == "route_unavailable"


@pytest.mark.anyio
async def test_agent_and_gateway_bind_share_access_channel_binding_truth(
    management_stack,
    tmp_path: Path,
) -> None:
    _manager, repo, router, dispatcher = management_stack
    codec = AcpCodec()
    repo.declare_adapter(
        adapter_id="test",
        adapter_type="test",
        config={},
        enabled=True,
        actor="primary",
    )

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"ok": True}

    router.register_runtime(_register(agent_id="primary"), handler)

    await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_ADD,
        {"agentId": "worker", "workspace": str(tmp_path / "workspace")},
        request_id=1,
    )
    agent_bind = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_BIND,
        {"agentId": "worker", "bind": "test:chan-1", "sessionId": "s-chan-1"},
        request_id=2,
    )
    assert agent_bind["result"]["binding"]["bindingId"] == "test:chan-1"

    gateway_view = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_BINDINGS,
        {"gatewayId": "test"},
        request_id=3,
    )
    assert gateway_view["result"]["bindings"] == [
        {
            "bindingId": "test:chan-1",
            "gatewayId": "test",
            "adapterId": "test",
            "channelKey": "chan-1",
            "targetAgentId": "worker",
            "targetSessionId": "s-chan-1",
            "enabled": True,
            "revision": 1,
            "updatedAt": gateway_view["result"]["bindings"][0]["updatedAt"],
            "updatedByAgentId": "primary",
        }
    ]

    duplicate_gateway_bind = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_BIND,
        {"gatewayId": "test", "channelKey": "chan-1", "agentId": "primary"},
        request_id=4,
    )
    assert duplicate_gateway_bind["error"]["code"] == -32602

    unknown_gateway = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_STATUS,
        {"gatewayId": "missing"},
        request_id=5,
    )
    assert unknown_gateway["error"]["code"] == -32602

    failed_reload = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_RELOAD,
        {"gatewayId": "test", "fail": True},
        request_id=6,
    )
    assert failed_reload["result"]["status"] == "failed"
    assert repo.adapter_event_count("test") == 2
    assert AccessRouterControlAPI(router).health().ready is True

    gateway_bind = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_BIND,
        {"gatewayId": "test", "channelKey": "chan-2", "agentId": "primary"},
        request_id=7,
    )
    assert gateway_bind["result"]["binding"]["bindingId"] == "test:chan-2"
    agent_view = await _request(
        dispatcher,
        codec,
        MustangMethod.AGENTS_BINDINGS,
        {"agentId": "primary"},
        request_id=8,
    )
    assert {row["bindingId"] for row in agent_view["result"]["bindings"]} == {"test:chan-2"}


@pytest.mark.anyio
async def test_gateway_create_delete_disables_bindings(
    management_stack,
) -> None:
    _manager, repo, _router, dispatcher = management_stack
    codec = AcpCodec()

    created = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_CREATE,
        {
            "gatewayId": "slack",
            "gatewayType": "test",
            "config": {"workspace": "T1"},
        },
        request_id=1,
    )
    assert created["result"]["gateway"]["gatewayId"] == "slack"
    assert created["result"]["gateway"]["revision"] == 1

    duplicate = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_CREATE,
        {"gatewayId": "slack", "gatewayType": "test"},
        request_id=2,
    )
    assert duplicate["error"]["code"] == -32602

    await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_BIND,
        {"gatewayId": "slack", "channelKey": "alerts", "agentId": "primary"},
        request_id=3,
    )
    rejected = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_DELETE,
        {"gatewayId": "slack", "confirm": False},
        request_id=4,
    )
    assert rejected["error"]["code"] == -32602

    deleted = await _request(
        dispatcher,
        codec,
        MustangMethod.GATEWAYS_DELETE,
        {"gatewayId": "slack", "confirm": True},
        request_id=5,
    )
    assert deleted["result"] == {
        "gatewayId": "slack",
        "deleted": True,
        "revision": 2,
        "disabledBindings": 1,
    }
    assert repo.get_adapter("slack") is None
    assert repo.list_channel_bindings(adapter_id="slack") == []
    assert (
        repo.list_channel_bindings(adapter_id="slack", include_disabled=True)[0]["enabled"] is False
    )
    assert repo.adapter_event_count("slack") == 2


def _register(agent_id: str) -> RuntimeRegisterRequest:
    return RuntimeRegisterRequest(
        process_id=f"runtime-{agent_id}",
        pid=123,
        agent_id=agent_id,
        protocol_version=1,
        capabilities=("session",),
        auth_token="secret",
    )
