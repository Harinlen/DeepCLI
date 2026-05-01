from __future__ import annotations

import pytest

from kernel.agent_runtime import (
    AgentResourceView,
    MinimalAgentRuntimeServer,
    request_runtime,
)
from kernel.agents import HubFrame, HubFrameType

pytestmark = pytest.mark.anyio


async def test_agent_resource_view_refreshes_only_changed_revisions() -> None:
    revisions = {"skills.global": 0}
    reloads: list[dict[str, int]] = []
    view = AgentResourceView(lambda: dict(revisions), reloads.append)

    assert await view.check_and_refresh_before_turn() == {"skills.global": 0}
    assert await view.check_and_refresh_before_turn() == {}

    revisions["skills.global"] = 1
    assert await view.check_and_refresh_before_turn() == {"skills.global": 1}
    assert reloads == [{"skills.global": 0}, {"skills.global": 1}]


async def test_minimal_agent_runtime_websocket_contract() -> None:
    server = MinimalAgentRuntimeServer()
    await server.start()
    try:
        response = await request_runtime(
            server.endpoint,
            HubFrame(
                frame_id="runtime-1",
                frame_type=HubFrameType.REQUEST,
                contract="runtime.ping",
            ),
        )
        assert response.frame_type == HubFrameType.RESPONSE
        assert response.payload == {"ok": True}
        assert response.correlation_id == "runtime-1"
    finally:
        await server.stop()
