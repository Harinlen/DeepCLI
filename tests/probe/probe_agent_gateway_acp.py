from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from kernel.access_router.gateway_commands import GatewayCommandService
from kernel.access_router.repository import AccessRouterRepository
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeRegisterRequest
from kernel.agent_hub.manager.command_surface import AgentCommandService
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agents.access.security.context import AuthContext
from kernel.core.protocol.acp.codec import AcpCodec
from kernel.core.protocol.acp.namespaces import MustangMethod
from kernel.core.protocol.acp.session_handler import AcpSessionHandler
from kernel.core.storage import ResourceStore, tables


class _ModuleTable:
    def __init__(
        self,
        *,
        agents: AgentCommandService,
        gateways: GatewayCommandService,
    ) -> None:
        self.agent_command_service = agents
        self.gateway_command_service = gateways


def _auth(connection_id: str) -> AuthContext:
    return AuthContext(
        connection_id=connection_id,
        credential_type="token",
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )


async def _request(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    method: str,
    params: dict[str, Any],
    *,
    request_id: int,
) -> dict[str, Any]:
    auth = _auth(f"agent-gateway-probe-{request_id}")
    init = codec.decode(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "probe", "title": "Probe"},
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


async def _main() -> None:
    with tempfile.TemporaryDirectory(prefix="mustang-agent-gateway-probe-") as raw_home:
        home = Path(raw_home)
        manager = AgentManager(home=home)
        manager.startup()
        repo = AccessRouterRepository.open(home)
        router = AccessRouter(auth_token="secret")
        seen: list[DeliverTurnRequest] = []

        async def handler(request: DeliverTurnRequest) -> dict[str, object]:
            seen.append(request)
            return {"reply": f"from-{request.agent_id}"}

        router.register_runtime(_register("worker"), handler)
        agents = AgentCommandService(manager=manager, gateway_repository=repo, router=router)
        gateways = GatewayCommandService(repo)
        dispatcher = AcpSessionHandler(_ModuleTable(agents=agents, gateways=gateways))
        codec = AcpCodec()
        try:
            created_gateway = await _request(
                dispatcher,
                codec,
                MustangMethod.GATEWAYS_CREATE,
                {"gatewayId": "test", "gatewayType": "test"},
                request_id=1,
            )
            await _request(
                dispatcher,
                codec,
                MustangMethod.AGENTS_ADD,
                {"agentId": "worker", "workspace": str(home / "workspace")},
                request_id=2,
            )
            agent_bind = await _request(
                dispatcher,
                codec,
                MustangMethod.AGENTS_BIND,
                {"agentId": "worker", "bind": "test:chan-1"},
                request_id=3,
            )
            gateway_bindings = await _request(
                dispatcher,
                codec,
                MustangMethod.GATEWAYS_BINDINGS,
                {"gatewayId": "test"},
                request_id=4,
            )
            send = await _request(
                dispatcher,
                codec,
                MustangMethod.AGENT_SEND,
                {"agentId": "worker", "message": "hello routed agent"},
                request_id=5,
            )
            unavailable = await _request(
                dispatcher,
                codec,
                MustangMethod.AGENT_SEND,
                {"agentId": "ghost", "message": "hello"},
                request_id=6,
            )
            deleted_gateway = await _request(
                dispatcher,
                codec,
                MustangMethod.GATEWAYS_DELETE,
                {"gatewayId": "test", "confirm": True},
                request_id=7,
            )

            store = ResourceStore.open(home)
            try:
                access_bindings = store.read_tx(
                    lambda conn: conn.execute(
                        sa.select(sa.func.count()).select_from(tables.access_channel_bindings)
                    ).fetchone()[0]
                )
                agent_bindings = store.read_tx(
                    lambda conn: conn.execute(
                        sa.select(sa.func.count()).select_from(tables.agent_bindings)
                    ).fetchone()[0]
                )
            finally:
                store.close()

            checks = {
                "gateway_created": created_gateway["result"]["gateway"]["gatewayId"] == "test",
                "agent_bind_result": agent_bind["result"]["binding"]["bindingId"],
                "gateway_view_same_binding": gateway_bindings["result"]["bindings"][0]["bindingId"]
                == "test:chan-1",
                "send_delivered": send["result"]["delivered"],
                "send_seen_by_access_router": bool(seen) and seen[0].prompt == "hello routed agent",
                "send_route_unavailable_typed": unavailable["result"]["errorCode"]
                == "route_unavailable",
                "gateway_deleted": deleted_gateway["result"]["deleted"],
                "gateway_delete_disabled_bindings": deleted_gateway["result"]["disabledBindings"]
                == 1,
                "access_channel_bindings": access_bindings,
                "agent_bindings": agent_bindings,
                "agent_hub_forward_count": router.agent_hub_forward_count,
            }
            for key, value in checks.items():
                print(f"{key}={value}")

            assert checks["gateway_created"] is True
            assert checks["gateway_view_same_binding"] is True
            assert checks["send_delivered"] is True
            assert checks["send_seen_by_access_router"] is True
            assert checks["send_route_unavailable_typed"] is True
            assert checks["gateway_deleted"] is True
            assert checks["gateway_delete_disabled_bindings"] is True
            assert checks["access_channel_bindings"] == 1
            assert checks["agent_bindings"] == 0
            assert checks["agent_hub_forward_count"] == 0
            print("probe=agent_gateway_acp result=PASS")
        finally:
            router.close()
            repo.close()
            manager.close()


def _register(agent_id: str) -> RuntimeRegisterRequest:
    return RuntimeRegisterRequest(
        process_id=f"runtime-{agent_id}",
        pid=123,
        agent_id=agent_id,
        protocol_version=1,
        capabilities=("session",),
        auth_token="secret",
    )


if __name__ == "__main__":
    asyncio.run(_main())
