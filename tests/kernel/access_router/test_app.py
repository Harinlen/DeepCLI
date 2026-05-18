from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from kernel.access_router.app import create_app
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeAcpRequest, RuntimeRegisterRequest


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
