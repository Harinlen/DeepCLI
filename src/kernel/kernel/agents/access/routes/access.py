"""FastAPI routes owned by Access Agent."""

from __future__ import annotations

from fastapi import APIRouter, Request

from kernel.agents.access.state import AccessAgentState

router = APIRouter(prefix="/access", tags=["access"])


def _state(request: Request) -> AccessAgentState:
    return request.app.state.access_agent_state


@router.get("/metadata")
async def metadata(request: Request) -> dict[str, object]:
    """Return Access Agent metadata."""

    return _state(request).metadata()


@router.get("/readiness")
async def readiness(request: Request) -> dict[str, object]:
    """Return detailed Access Agent readiness."""

    state = _state(request)
    await state.refresh_from_hub()
    payload = state.readiness()
    error = state.startup_error()
    if error is not None:
        payload["startup_error"] = error
    return payload
