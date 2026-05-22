"""Probe Agent health freshness through real AccessRouter and AgentManager."""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import sys
import tempfile
from pathlib import Path

import sqlalchemy as sa
import uvicorn.protocols.websockets.websockets_impl as uvicorn_ws

from kernel.access_router.router import AccessRouter, RouteUnavailable
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeRegisterRequest
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agent_hub.manager.schemas import CreateAgentSpec
from kernel.core.storage import ResourceStore, tables


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mustang-agent-health-") as tmp:
        home = Path(tmp) / "resource"
        router = AccessRouter(auth_token="secret", stale_timeout_seconds=15.0)
        manager = AgentManager(home=home, route_status_reader=router.route_status)
        manager.startup()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            created = manager.create(
                CreateAgentSpec(
                    agent_id="worker",
                    name="Worker",
                    workspace=Path(tmp) / "workspace",
                    state_dir=home / "agents" / "worker",
                ),
                actor_agent_id="primary",
            )
            manager._processes["worker"] = process
            registered = router.register_runtime(
                RuntimeRegisterRequest(
                    process_id="worker-runtime",
                    pid=process.pid,
                    agent_id="worker",
                    protocol_version=1,
                    capabilities=("session",),
                    auth_token="secret",
                    role="agent_runtime",
                ),
                _turn_handler,
            )

            fresh = manager.health("worker")
            fresh_route_reports_fresh = (
                fresh.healthy is True and fresh.runtime_heartbeat_fresh is True
            )

            router._stale_timeout_seconds = -1
            stale = manager.health("worker")
            stale_route_reports_stale = (
                stale.healthy is False
                and stale.reason == "heartbeat_stale"
                and stale.route_status == "stale"
            )
            try:
                await router.deliver_turn(_turn())
                stale_route_rejected = False
            except RouteUnavailable:
                stale_route_rejected = True

            router.unregister_runtime(registered.connection_id)
            missing = manager.health("worker")
            missing_route_unavailable = (
                missing.healthy is False and missing.reason == "route_unavailable"
            )

            router._stale_timeout_seconds = 15.0
            router.register_runtime(
                RuntimeRegisterRequest(
                    process_id="worker-runtime-2",
                    pid=process.pid,
                    agent_id="worker",
                    protocol_version=1,
                    capabilities=("session",),
                    auth_token="secret",
                    role="agent_runtime",
                ),
                _turn_handler,
            )
            reconnect = manager.health("worker")
            reconnect_restores_fresh = (
                reconnect.healthy is True and reconnect.runtime_heartbeat_fresh is True
            )

            deleted = manager.delete(
                "worker",
                expected_revision=created.revision,
                actor_agent_id="primary",
                confirm=True,
            )
            deleted_health = manager.health("worker")
            deleted_agent_health_deleted = (
                deleted.deleted is True
                and deleted_health.healthy is False
                and deleted_health.reason == "deleted"
            )
            agent_bindings = _agent_bindings_count(home)
        finally:
            manager.close()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    native_pong_observable = _native_pong_observable()
    no_acp_heartbeat_added = "_mustang.runtime/ping" not in (
        Path("src/kernel/kernel/agents/mustang/runtime/__main__.py").read_text(encoding="utf-8")
    )
    result = all(
        [
            native_pong_observable is False,
            fresh_route_reports_fresh,
            stale_route_reports_stale,
            stale_route_rejected,
            missing_route_unavailable,
            deleted_agent_health_deleted,
            reconnect_restores_fresh,
            no_acp_heartbeat_added,
            agent_bindings == 0,
        ]
    )
    print("probe=agent_health_heartbeat_freshness")
    print(f"native_pong_observable={str(native_pong_observable).lower()}")
    print(f"fresh_route_reports_fresh={fresh_route_reports_fresh}")
    print(f"stale_route_reports_stale={stale_route_reports_stale}")
    print(f"stale_route_rejected={stale_route_rejected}")
    print(f"missing_route_unavailable={missing_route_unavailable}")
    print(f"deleted_agent_health_deleted={deleted_agent_health_deleted}")
    print(f"reconnect_restores_fresh={reconnect_restores_fresh}")
    print(f"no_acp_heartbeat_added={no_acp_heartbeat_added}")
    print(f"agent_bindings={agent_bindings}")
    print(f"result={'PASS' if result else 'FAIL'}")
    if not result:
        raise SystemExit(1)


async def _turn_handler(_: DeliverTurnRequest) -> dict[str, object]:
    return {"ok": True}


def _turn() -> DeliverTurnRequest:
    return DeliverTurnRequest(
        agent_id="worker",
        session_id="probe-session",
        client_turn_id="probe-turn",
        prompt="hello",
    )


def _agent_bindings_count(home: Path) -> int:
    store = ResourceStore.open(home)
    try:
        return int(
            store.read_tx(
                lambda conn: conn.execute(
                    sa.select(sa.func.count()).select_from(tables.agent_bindings)
                ).fetchone()[0]
            )
        )
    finally:
        store.close()


def _native_pong_observable() -> bool:
    source = inspect.getsource(uvicorn_ws.WebSocketProtocol.asgi_receive)
    return "pong" in source.lower() or "websocket.pong" in source


if __name__ == "__main__":
    asyncio.run(main())
