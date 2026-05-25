from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kernel.access_router.app import create_app
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeAcpRequest, RuntimeRegisterRequest
from kernel.core.secrets import SecretManager


async def _turn_handler(
    _request: DeliverTurnRequest,
    _client_request_proxy: Any | None = None,
) -> dict[str, object]:
    return {"ok": True}


async def _acp_handler(
    _request: RuntimeAcpRequest,
    _client_request_proxy: Any | None = None,
) -> dict[str, object]:
    return {"ok": True}


def test_access_readiness_reports_primary_not_ready_before_registration() -> None:
    app = create_app(AccessRouter(auth_token="token"))

    with TestClient(app) as client:
        response = client.get("/access/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["process_ready"] is True
    assert payload["default_route_ready"] is False
    assert payload["primary_registered"] is False
    assert payload["route_status"]["agent_id"] == "primary"
    assert payload["route_status"]["status"] == "unavailable"


def test_health_reports_version_metadata() -> None:
    app = create_app(AccessRouter(auth_token="token"))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["name"] == "deepcli-access-router"
    assert isinstance(payload["version"], str)
    assert isinstance(payload["boot_time"], float)


def test_web_bridge_status_is_served_without_primary_registration() -> None:
    app = create_app(AccessRouter(auth_token="token"))

    with TestClient(app) as client:
        response = client.get("/web-bridge/status.json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "setup_needed"
    assert payload["paired"] is False
    assert payload["connected"] is False
    assert payload["bridgeWsUrl"].startswith("ws://127.0.0.1:")
    assert payload["installUrl"].endswith("/web-bridge/install")
    assert payload["pairingToken"] is None


def test_web_bridge_pairing_secret_survives_access_router_restart(tmp_path) -> None:
    secrets = SecretManager(home=tmp_path)
    asyncio.run(secrets.startup())
    try:
        secrets.set(
            "web_bridge.extension.secret",
            "persisted-secret",
            kind="web_bridge",
            metadata={"scope": "web_bridge", "extension_id": "ext-1"},
        )
    finally:
        secrets.close()

    first_app = create_app(AccessRouter(auth_token="token"), resource_home=str(tmp_path))
    with TestClient(first_app) as client:
        first_status = client.get("/web-bridge/status.json").json()

    second_app = create_app(AccessRouter(auth_token="token"), resource_home=str(tmp_path))
    with TestClient(second_app) as client:
        second_status = client.get("/web-bridge/status.json").json()

    assert first_status["status"] == "configured"
    assert first_status["paired"] is True
    assert first_status["connected"] is False
    assert second_status["status"] == "configured"
    assert second_status["paired"] is True
    assert second_status["connected"] is False


def test_bus_topology_reports_global_resources_without_primary_registration() -> None:
    app = create_app(AccessRouter(auth_token="token"))

    with TestClient(app) as client:
        response = client.get("/bus/topology")

    assert response.status_code == 200
    payload = response.json()
    services = {service["serviceId"]: service for service in payload["services"]}
    assert services["resource:web_bridge"]["owner"] == "GlobalResourceHost"
    assert services["resource:web_bridge"]["routeReady"] is True
    assert services["resource:web_search"]["owner"] == "GlobalResourceHost"
    assert all("contract" not in service for service in services.values())


def test_session_bus_topology_snapshot_is_handled_locally() -> None:
    app = create_app(AccessRouter(auth_token="token"))

    with TestClient(app) as client:
        with client.websocket_connect("/session") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "init",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "probe", "version": "1.0.0"},
                    },
                }
            )
            assert websocket.receive_json()["id"] == "init"
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "topology",
                    "method": "_mustang.bus/topology.snapshot",
                    "params": {},
                }
            )
            response = websocket.receive_json()

    assert response["id"] == "topology"
    services = {service["serviceId"]: service for service in response["result"]["services"]}
    assert "resource:web_bridge" in services
    assert "resource:web_search" in services


def test_access_readiness_reports_primary_ready_after_registration() -> None:
    router = AccessRouter(auth_token="token")
    router.register_runtime(
        RuntimeRegisterRequest(
            process_id="primary-runtime",
            agent_id="primary",
            auth_token="token",
            pid=123,
            protocol_version=1,
            role="agent_runtime",
        ),
        _turn_handler,
        _acp_handler,
    )
    app = create_app(router)

    with TestClient(app) as client:
        response = client.get("/access/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["process_ready"] is True
    assert payload["default_route_ready"] is True
    assert payload["primary_registered"] is True
    assert payload["registered_agents"] == 1
    assert payload["route_status"]["agent_id"] == "primary"
    assert payload["route_status"]["status"] == "registered"


def test_runtime_websocket_unregisters_route_on_disconnect() -> None:
    router = AccessRouter(auth_token="token")
    app = create_app(router)

    with TestClient(app) as client:
        with client.websocket_connect("/runtime") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "register",
                    "method": "_mustang.router/register_runtime",
                    "params": RuntimeRegisterRequest(
                        process_id="primary-runtime",
                        agent_id="primary",
                        auth_token="token",
                        pid=123,
                        protocol_version=1,
                        role="agent_runtime",
                    ).model_dump(),
                }
            )
            ack = websocket.receive_json()
            assert ack["ok"] is True
            assert client.get("/access/readiness").json()["primary_registered"] is True

        assert client.get("/access/readiness").json()["primary_registered"] is False


def test_runtime_websocket_rejects_invalid_auth_without_server_exception() -> None:
    router = AccessRouter(auth_token="token")
    app = create_app(router)

    with TestClient(app) as client:
        with client.websocket_connect("/runtime") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "register",
                    "method": "_mustang.router/register_runtime",
                    "params": RuntimeRegisterRequest(
                        process_id="primary-runtime",
                        agent_id="primary",
                        auth_token="wrong-token",
                        pid=123,
                        protocol_version=1,
                        role="agent_runtime",
                    ).model_dump(),
                }
            )
            error = websocket.receive_json()
            assert error == {
                "ok": False,
                "error": "unauthorized",
                "message": "invalid runtime auth token",
            }
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()

    assert exc_info.value.code == 1008
    assert router.route_status("primary").status == "unavailable"


def test_runtime_websocket_rejects_invalid_registration_without_server_exception() -> None:
    router = AccessRouter(auth_token="token")
    app = create_app(router)

    with TestClient(app) as client:
        with client.websocket_connect("/runtime") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "register",
                    "method": "_mustang.router/register_runtime",
                    "params": {
                        "process_id": "primary-runtime",
                        "agent_id": "primary",
                        "auth_token": "token",
                        "pid": 123,
                        "protocol_version": 999,
                        "role": "agent_runtime",
                    },
                }
            )
            error = websocket.receive_json()
            assert error == {
                "ok": False,
                "error": "invalid_runtime_registration",
                "message": "unsupported runtime protocol version",
            }
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()

    assert exc_info.value.code == 1002
    assert router.route_status("primary").status == "unavailable"


def test_runtime_websocket_ping_refreshes_idle_stale_route() -> None:
    router = AccessRouter(auth_token="token", stale_timeout_seconds=0.03)
    app = create_app(router)

    with TestClient(app) as client:
        with client.websocket_connect("/runtime") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "register",
                    "method": "_mustang.router/register_runtime",
                    "params": RuntimeRegisterRequest(
                        process_id="primary-runtime",
                        agent_id="primary",
                        auth_token="token",
                        pid=123,
                        protocol_version=1,
                        role="agent_runtime",
                    ).model_dump(),
                }
            )
            ack = websocket.receive_json()
            connection_id = ack["result"]["connection_id"]
            time.sleep(0.05)
            assert client.get("/route_status/primary").json()["status"] == "stale"

            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "method": "_mustang.router/ping",
                    "params": {"connection_id": connection_id},
                }
            )
            time.sleep(0.01)

            assert client.get("/route_status/primary").json()["status"] == "registered"


def test_runtime_control_methods_are_handled_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kernel.supervisor import control as supervisor_control

    monkeypatch.setenv("MUSTANG_SUPERVISOR_CONTROL_SOCKET", "/tmp/deepcli-control.sock")
    monkeypatch.setenv("MUSTANG_SUPERVISOR_CONTROL_TOKEN", "control-token")
    calls: list[tuple[str, dict[str, object]]] = []

    def _request_control(
        socket_path: str,
        token: str,
        method: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        assert socket_path == "/tmp/deepcli-control.sock"
        assert token == "control-token"
        calls.append((method, params or {}))
        return {
            "ok": True,
            "status": "ready",
            "children": {"access_router": {"pid": 123, "running": True}},
        }

    monkeypatch.setattr(supervisor_control, "request_control", _request_control)

    router = AccessRouter(auth_token="token")
    registered = router.register_runtime(
        RuntimeRegisterRequest(
            process_id="primary-runtime",
            agent_id="primary",
            auth_token="token",
            pid=123,
            protocol_version=1,
            role="agent_runtime",
        ),
        _turn_handler,
        _acp_handler,
    )
    app = create_app(router)

    with TestClient(app) as client:
        with client.websocket_connect("/session") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "init",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "probe", "version": "1.0.0"},
                    },
                }
            )
            assert websocket.receive_json()["id"] == "init"
            router.unregister_runtime(registered.connection_id)

            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "status",
                    "method": "_mustang.agent/runtime/status",
                    "params": {},
                }
            )
            status = websocket.receive_json()
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "restart",
                    "method": "_mustang.agent/runtime/restart",
                    "params": {"reason": "probe restart"},
                }
            )
            restart = websocket.receive_json()

    assert status["result"]["status"]["status"] == "ready"
    assert restart["result"]["status"]["children"]["access_router"]["running"] is True
    assert calls == [
        ("status", {}),
        ("restart_runtime", {"reason": "probe restart"}),
    ]
