from __future__ import annotations

import pytest

from kernel.access_router.control_api import AccessRouterControlAPI
from kernel.access_router.router import AccessRouter, RouteUnavailable
from kernel.access_router.schemas import DeliverTurnRequest, RuntimePing, RuntimeRegisterRequest

pytestmark = pytest.mark.anyio


async def test_runtime_register_rejects_bad_token_and_version() -> None:
    router = AccessRouter(auth_token="secret")

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"ok": True}

    with pytest.raises(PermissionError):
        router.register_runtime(_register(auth_token="bad"), handler)
    with pytest.raises(ValueError, match="protocol"):
        router.register_runtime(_register(protocol_version=99), handler)


async def test_route_unavailable_error() -> None:
    router = AccessRouter(auth_token="secret")
    with pytest.raises(RouteUnavailable, match="unavailable"):
        await router.deliver_turn(_turn())


async def test_stale_connection_eviction() -> None:
    router = AccessRouter(auth_token="secret", stale_timeout_seconds=-1)

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"ok": True}

    router.register_runtime(_register(), handler)

    assert router.route_status("primary").status == "stale"
    assert router.evict_stale() == ["primary"]
    assert router.route_status("primary").status == "unavailable"


async def test_stale_route_rejects_delivery() -> None:
    router = AccessRouter(auth_token="secret", stale_timeout_seconds=-1)

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"ok": True}

    router.register_runtime(_register(), handler)

    with pytest.raises(RouteUnavailable, match="route stale: primary"):
        await router.deliver_turn(_turn())


async def test_reconnect_restores_fresh_route() -> None:
    router = AccessRouter(auth_token="secret", stale_timeout_seconds=-1)

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"ok": True}

    first = router.register_runtime(_register(pid=111), handler)
    assert router.route_status("primary").status == "stale"

    router.unregister_runtime(first.connection_id)
    router._stale_timeout_seconds = 15.0
    second = router.register_runtime(_register(pid=222), handler)

    status = router.route_status("primary")
    assert status.status == "registered"
    assert status.connection_id == second.connection_id
    assert await router.deliver_turn(_turn()) == {"ok": True}


async def test_unregister_runtime_removes_closed_route() -> None:
    router = AccessRouter(auth_token="secret")

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"ok": True}

    registered = router.register_runtime(_register(), handler)
    assert router.route_status("primary").status == "registered"

    router.unregister_runtime(registered.connection_id)

    assert router.route_status("primary").status == "unavailable"


async def test_runtime_ping_pong_compatibility_path() -> None:
    router = AccessRouter(auth_token="secret")

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"ok": True}

    registered = router.register_runtime(_register(), handler)

    pong = router.ping(RuntimePing(connection_id=registered.connection_id))
    router.pong(pong)

    assert pong.ok is True
    assert router.route_status("primary").status == "registered"


async def test_observable_runtime_activity_refreshes_connection() -> None:
    router = AccessRouter(auth_token="secret", stale_timeout_seconds=-1)

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"ok": True}

    registered = router.register_runtime(_register(), handler)
    assert router.route_status("primary").status == "stale"

    router._stale_timeout_seconds = 15.0
    router.touch_runtime(registered.connection_id)

    assert router.route_status("primary").status == "registered"


async def test_idempotency_duplicate_returns_stored_status() -> None:
    router = AccessRouter(auth_token="secret")
    calls = 0

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"reply": f"call-{calls}"}

    router.register_runtime(_register(), handler)

    first = await router.deliver_turn(_turn(idempotency_key="same"))
    second = await router.deliver_turn(_turn(idempotency_key="same"))

    assert first == {"reply": "call-1"}
    assert second == first
    assert calls == 1


async def test_agent_hub_control_api_cannot_deliver_turns() -> None:
    router = AccessRouter(auth_token="secret")
    control = AccessRouterControlAPI(router)

    assert control.health().agent_hub_forward_count == 0
    assert control.route_status("primary").status == "unavailable"
    with pytest.raises(PermissionError, match="cannot deliver turns"):
        router.control_deliver_turn(_turn())
    assert router.agent_hub_forward_count == 0


async def test_registered_runtime_receives_turn() -> None:
    router = AccessRouter(auth_token="secret")
    seen: list[DeliverTurnRequest] = []

    async def handler(request: DeliverTurnRequest) -> dict[str, object]:
        seen.append(request)
        return {"reply": "from-primary"}

    result = router.register_runtime(_register(), handler)
    delivered = await router.deliver_turn(_turn())

    assert result.status == "registered"
    assert delivered == {"reply": "from-primary"}
    assert seen[0].prompt == "hello"
    assert [agent.agent_id for agent in router.registered_agents()] == ["primary"]


async def test_bus_topology_separates_agent_and_resource_ownership() -> None:
    router = AccessRouter(auth_token="secret")

    async def handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"reply": "from-primary"}

    router.register_runtime(_register(), handler)
    router.register_resource(
        "resource:web_bridge",
        capabilities=("_mustang.resource/web_bridge.status",),
    )

    snapshot = router.bus_topology_snapshot()
    services = {service.service_id: service for service in snapshot.services}

    assert services["agent:primary"].owner == "AgentRuntimeHost"
    assert services["agent:primary"].kind == "agent"
    assert services["resource:web_bridge"].owner == "GlobalResourceHost"
    assert services["resource:web_bridge"].kind == "resource"
    assert services["resource:web_bridge"].route_ready is True


async def test_main_is_not_a_primary_alias() -> None:
    router = AccessRouter(auth_token="secret", stale_timeout_seconds=1)

    async def primary_handler(_: DeliverTurnRequest) -> dict[str, object]:
        return {"reply": "from-primary"}

    router.register_runtime(_register(agent_id="primary", pid=222), primary_handler)

    status = router.route_status("main")

    assert status.status == "unavailable"
    with pytest.raises(RouteUnavailable, match="route unavailable: main"):
        await router.deliver_turn(_turn(agent_id="main"))


def _register(
    *,
    auth_token: str = "secret",
    protocol_version: int = 1,
    agent_id: str = "primary",
    pid: int = 123,
) -> RuntimeRegisterRequest:
    return RuntimeRegisterRequest(
        process_id=f"runtime-{agent_id}",
        pid=pid,
        agent_id=agent_id,
        protocol_version=protocol_version,
        capabilities=("session",),
        auth_token=auth_token,
    )


def _turn(
    *,
    idempotency_key: str | None = None,
    agent_id: str = "primary",
) -> DeliverTurnRequest:
    return DeliverTurnRequest(
        agent_id=agent_id,
        session_id="s-1",
        client_turn_id="turn-1",
        prompt="hello",
        idempotency_key=idempotency_key,
    )
