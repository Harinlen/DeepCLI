from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kernel.access_agent import AccessAgentState
from kernel.access_agent.routes import router


def _app(state: AccessAgentState) -> FastAPI:
    app = FastAPI()
    app.state.access_agent_state = state
    app.include_router(router)
    return app


def test_access_agent_readiness_reports_pre_primary_starting() -> None:
    state = AccessAgentState()
    state.mark_hub_ready()

    client = TestClient(_app(state))
    response = client.get("/access/readiness")

    payload = response.json()
    assert payload["process_ready"] is True
    assert payload["hub_ready"] is True
    assert payload["primary_registered"] is False
    assert payload["default_route_ready"] is False
    assert payload["startup_error"]["code"] == "primary_agent_starting"


def test_access_agent_metadata_and_default_route_ready() -> None:
    state = AccessAgentState()
    state.mark_hub_ready()
    state.mark_primary_registered()

    client = TestClient(_app(state))

    assert client.get("/access/metadata").json()["name"] == "mustang-access-agent"
    readiness = client.get("/access/readiness").json()
    assert readiness["default_route_ready"] is True
    assert "startup_error" not in readiness
