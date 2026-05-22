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
from kernel.agent_hub.manager.command_surface import AgentCommandService
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agent_hub.manager.schemas import GrantCapability, ResourceScope
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
    auth = _auth(f"agent-delete-probe-{request_id}")
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
    with tempfile.TemporaryDirectory(prefix="mustang-agent-delete-probe-") as raw_home:
        home = Path(raw_home)
        workspace = home / "workspace"
        workspace.mkdir()
        state_dir = home / "agents" / "worker"
        state_dir.mkdir(parents=True)
        (state_dir / "state.json").write_text("{}", encoding="utf-8")

        manager = AgentManager(home=home)
        manager.startup()
        repo = AccessRouterRepository.open(home)
        router = AccessRouter(auth_token="secret")
        agents = AgentCommandService(manager=manager, gateway_repository=repo, router=router)
        gateways = GatewayCommandService(repo)
        dispatcher = AcpSessionHandler(_ModuleTable(agents=agents, gateways=gateways))
        codec = AcpCodec()
        try:
            repo.declare_adapter(adapter_id="test", adapter_type="test", config={}, actor="primary")
            added = await _request(
                dispatcher,
                codec,
                MustangMethod.AGENTS_ADD,
                {"agentId": "worker", "workspace": str(workspace), "stateDir": str(state_dir)},
                request_id=1,
            )
            repo.set_channel_binding(
                binding_id="test:chan-1",
                adapter_id="test",
                channel_key="chan-1",
                target_agent_id="worker",
                actor="primary",
            )
            grant = manager.grant(
                "worker",
                GrantCapability.GLOBAL_RESOURCE_WRITE,
                ResourceScope.GLOBAL,
                granted_by_agent_id="primary",
            )
            rejected = await _request(
                dispatcher,
                codec,
                MustangMethod.AGENTS_DELETE,
                {"agentId": "worker", "confirm": False},
                request_id=2,
            )
            deleted = await _request(
                dispatcher,
                codec,
                MustangMethod.AGENTS_DELETE,
                {"agentId": "worker", "confirm": True},
                request_id=3,
            )
            start_deleted = await _request(
                dispatcher,
                codec,
                MustangMethod.AGENTS_START,
                {"agentId": "worker", "routerEndpoint": "ws://127.0.0.1:1", "routerToken": "t"},
                request_id=4,
            )

            store = ResourceStore.open(home)
            try:
                row = store.read_tx(
                    lambda conn: conn.execute(
                        sa.select(tables.agent_definitions).where(
                            tables.agent_definitions.c.agent_id == "worker"
                        )
                    ).fetchone()
                )
                binding_enabled = store.read_tx(
                    lambda conn: conn.execute(
                        sa.select(tables.access_channel_bindings.c.enabled).where(
                            tables.access_channel_bindings.c.binding_id == "test:chan-1"
                        )
                    ).fetchone()[0]
                )
                grant_revoked = store.read_tx(
                    lambda conn: conn.execute(
                        sa.select(tables.management_grants.c.revoked_at).where(
                            tables.management_grants.c.grant_id == grant.grant_id
                        )
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
                "agent_added": added["result"]["agent"]["agentId"] == "worker",
                "delete_without_confirm_rejected": "error" in rejected,
                "delete_result_status": deleted["result"]["stateDirDeletionStatus"],
                "db_status": row["status"],
                "db_state_dir_deletion_status": row["state_dir_deletion_status"],
                "binding_disabled": bool(binding_enabled) is False,
                "grant_revoked": grant_revoked is not None,
                "workspace_exists": workspace.exists(),
                "state_dir_removed": not state_dir.exists(),
                "start_deleted_rejected": "error" in start_deleted,
                "agent_bindings": agent_bindings,
            }
            for key, value in checks.items():
                print(f"{key}={value}")

            assert checks["agent_added"] is True
            assert checks["delete_without_confirm_rejected"] is True
            assert checks["delete_result_status"] == "deleted"
            assert checks["db_status"] == "deleted"
            assert checks["db_state_dir_deletion_status"] == "deleted"
            assert checks["binding_disabled"] is True
            assert checks["grant_revoked"] is True
            assert checks["workspace_exists"] is True
            assert checks["state_dir_removed"] is True
            assert checks["start_deleted_rejected"] is True
            assert checks["agent_bindings"] == 0
            print("probe=agent_delete_lifecycle result=PASS")
        finally:
            router.close()
            repo.close()
            manager.close()


if __name__ == "__main__":
    asyncio.run(_main())
