from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kernel.access_router.app import create_app
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import (
    DeliverTurnRequest,
    RuntimeAcpRequest,
    RuntimeRegisterRequest,
)
from kernel.access_router.router import ClientRequestProxy

pytestmark = pytest.mark.e2e


@pytest.mark.anyio
async def test_access_router_local_path_reaches_primary_runtime() -> None:
    router = AccessRouter(auth_token="secret")
    session_log: list[dict[str, object]] = []

    async def primary_runtime(request: DeliverTurnRequest) -> dict[str, object]:
        session_log.append(
            {
                "session_id": request.session_id,
                "client_turn_id": request.client_turn_id,
                "prompt": request.prompt,
            }
        )
        return {"agent_id": request.agent_id, "text": "primary reply"}

    async def primary_acp_runtime(_request: RuntimeAcpRequest) -> dict[str, object]:
        return {"protocolVersion": 1, "agentInfo": {"name": "mustang-agent-runtime"}}

    router.register_runtime(
        RuntimeRegisterRequest(
            process_id="runtime-primary",
            pid=123,
            agent_id="primary",
            protocol_version=1,
            capabilities=("session",),
            auth_token="secret",
        ),
        primary_runtime,
        primary_acp_runtime,
    )

    app = create_app(router)
    with TestClient(app) as client:
        with client.websocket_connect("/session") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "probe", "version": "1.0.0"},
                    },
                }
            )
            init_response = websocket.receive_json()
            assert init_response["id"] == "init-1"
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "rpc-1",
                    "method": "_mustang.client/turn",
                    "params": {
                        "agent_id": "primary",
                        "session_id": "s-e2e",
                        "client_turn_id": "turn-e2e",
                        "prompt": "hello",
                        "idempotency_key": "local-e2e",
                    },
                }
            )
            response = websocket.receive_json()

    assert response == {
        "jsonrpc": "2.0",
        "id": "rpc-1",
        "result": {"agent_id": "primary", "text": "primary reply"},
    }
    assert session_log == [
        {"session_id": "s-e2e", "client_turn_id": "turn-e2e", "prompt": "hello"}
    ]
    assert router.agent_hub_forward_count == 0


def test_access_router_forwards_initialize_to_primary_runtime() -> None:
    router = AccessRouter(auth_token="secret")
    acp_log: list[RuntimeAcpRequest] = []

    async def primary_runtime(_: DeliverTurnRequest) -> dict[str, object]:
        return {"text": "unused"}

    async def primary_acp_runtime(request: RuntimeAcpRequest) -> dict[str, object]:
        acp_log.append(request)
        return {
            "protocolVersion": 1,
            "agentInfo": {"name": "mustang-agent-runtime"},
            "agentCapabilities": {"loadSession": True},
        }

    router.register_runtime(
        RuntimeRegisterRequest(
            process_id="runtime-primary",
            pid=123,
            agent_id="primary",
            protocol_version=1,
            capabilities=("session", "acp"),
            auth_token="secret",
        ),
        primary_runtime,
        primary_acp_runtime,
    )

    app = create_app(router)
    with TestClient(app) as client:
        with client.websocket_connect("/session") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "deepcli-cli", "version": "1.0.0"},
                    },
                }
            )
            response = websocket.receive_json()

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "init-1"
    assert response["result"]["agentInfo"]["name"] == "mustang-agent-runtime"
    assert [request.method for request in acp_log] == ["initialize"]
    assert router.agent_hub_forward_count == 0


def test_access_router_handles_agents_management_locally(tmp_path: Path) -> None:
    router = AccessRouter(auth_token="secret")
    acp_log: list[RuntimeAcpRequest] = []

    async def primary_runtime(_: DeliverTurnRequest) -> dict[str, object]:
        return {"text": "unused"}

    async def primary_acp_runtime(request: RuntimeAcpRequest) -> dict[str, object]:
        acp_log.append(request)
        if request.method != "initialize":
            raise AssertionError(f"management request leaked to runtime: {request.method}")
        return {
            "protocolVersion": 1,
            "agentInfo": {"name": "mustang-agent-runtime"},
            "agentCapabilities": {"loadSession": True},
        }

    router.register_runtime(
        RuntimeRegisterRequest(
            process_id="runtime-primary",
            pid=123,
            agent_id="primary",
            protocol_version=1,
            capabilities=("session", "acp"),
            auth_token="secret",
        ),
        primary_runtime,
        primary_acp_runtime,
    )

    app = create_app(router, resource_home=str(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/session") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "probe", "version": "1.0.0"},
                    },
                }
            )
            init_response = websocket.receive_json()
            assert init_response["id"] == "init-1"
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "agents-1",
                    "method": "_mustang.agent/agents/list",
                    "params": {"includeBindings": True},
                }
            )
            agents_response = websocket.receive_json()
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "gateways-1",
                    "method": "_mustang.agent/gateways/list",
                    "params": {},
                }
            )
            gateways_response = websocket.receive_json()
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "mcp-1",
                    "method": "_mustang.agent/mcp/list",
                    "params": {},
                }
            )
            mcp_response = websocket.receive_json()

    assert agents_response["id"] == "agents-1"
    assert agents_response["result"]["agents"][0]["agentId"] == "primary"
    assert agents_response["result"]["bindings"] == []
    assert gateways_response == {
        "jsonrpc": "2.0",
        "id": "gateways-1",
        "result": {"gateways": []},
    }
    assert mcp_response["id"] == "mcp-1"
    assert mcp_response["result"] == {"servers": [], "revision": 0}
    assert [request.method for request in acp_log] == ["initialize"]


def test_access_router_proxies_runtime_client_permission_request() -> None:
    router = AccessRouter(auth_token="secret")

    async def primary_runtime(
        _request: DeliverTurnRequest,
        _client_request_proxy: ClientRequestProxy | None = None,
    ) -> dict[str, object]:
        return {"text": "unused"}

    async def primary_acp_runtime(
        request: RuntimeAcpRequest,
        client_request_proxy: ClientRequestProxy | None = None,
    ) -> dict[str, object]:
        if request.method == "initialize":
            return {"protocolVersion": 1, "agentInfo": {"name": "mustang-agent-runtime"}}
        assert client_request_proxy is not None
        permission = await client_request_proxy(
            "session/request_permission",
            {
                "sessionId": "s-e2e",
                "toolCall": {"toolCallId": "tool-1", "title": "Bash"},
                "options": [
                    {"optionId": "reject", "name": "Reject", "kind": "reject_once"}
                ],
            },
        )
        return {"ok": True, "permission": permission}

    router.register_runtime(
        RuntimeRegisterRequest(
            process_id="runtime-primary",
            pid=123,
            agent_id="primary",
            protocol_version=1,
            capabilities=("session", "acp"),
            auth_token="secret",
        ),
        primary_runtime,
        primary_acp_runtime,
    )

    app = create_app(router)
    with TestClient(app) as client:
        with client.websocket_connect("/session") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "probe", "version": "1.0.0"},
                    },
                }
            )
            init_response = websocket.receive_json()
            assert init_response["id"] == "init-1"
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "prompt-1",
                    "method": "session/prompt",
                    "params": {
                        "agent_id": "primary",
                        "sessionId": "s-e2e",
                        "prompt": [{"type": "text", "text": "needs permission"}],
                    },
                }
            )
            permission_request = websocket.receive_json()
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": permission_request["id"],
                    "result": {
                        "outcome": {"outcome": "selected", "optionId": "reject"}
                    },
                }
            )
            response = websocket.receive_json()

    assert permission_request["method"] == "session/request_permission"
    assert response["id"] == "prompt-1"
    assert response["result"]["permission"]["outcome"]["optionId"] == "reject"
    assert router.agent_hub_forward_count == 0
