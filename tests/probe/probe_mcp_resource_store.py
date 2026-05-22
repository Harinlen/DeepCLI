from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any

import orjson
import yaml

from kernel.agents.access.security.context import AuthContext
from kernel.agents.mustang.mcp import MCPManager
from kernel.agents.mustang.mcp.config import (
    MCPConfig,
)
from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.core.config import ConfigManager
from kernel.core.flags import FlagManager
from kernel.core.protocol.acp.codec import AcpCodec
from kernel.core.protocol.acp.namespaces import MustangMethod
from kernel.core.protocol.acp.session_handler import AcpSessionHandler
from kernel.core.storage import ResourceStore


async def _module_table(home: Path) -> KernelModuleTable:
    flags = FlagManager(resource_home=home)
    await flags.initialize()
    config = ConfigManager(resource_home=home)
    await config.startup()
    state_dir = home / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return KernelModuleTable(flags=flags, config=config, state_dir=state_dir)


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
    auth = _auth(f"mcp-resource-probe-{request_id}")
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
    with tempfile.TemporaryDirectory(prefix="mustang-mcp-resource-probe-") as raw_home:
        home = Path(raw_home)
        legacy = home / "config" / "mcp.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            yaml.safe_dump(
                {"mcp": {"servers": {"legacy": {"command": "/nonexistent/mcp-server"}}}}
            ),
            encoding="utf-8",
        )

        mt = await _module_table(home)
        manager = MCPManager(mt)
        await manager.startup()
        try:
            mcp_startup_from_resource_store = "legacy" in manager.get_connections()
            revision_after_import = mt.config.current_revisions().get("config.global._.mcp.mcp")
        finally:
            await manager.shutdown()
            mt.config.close()

        legacy.write_text(
            yaml.safe_dump({"mcp": {"servers": {"drift": {"command": "node"}}}}),
            encoding="utf-8",
        )
        drift_config = ConfigManager(resource_home=home)
        await drift_config.startup()
        try:
            section = drift_config.get_section(file="mcp", section="mcp", schema=MCPConfig)
            current = section.get()
            legacy_import_once = "legacy" in current.servers and "drift" not in current.servers
            legacy_drift_ignored = (
                drift_config.legacy_migration_report is not None
                and drift_config.legacy_migration_report.drift == ("legacy:mcp.yaml",)
            )
        finally:
            drift_config.close()

        mt = await _module_table(home)
        dispatcher = AcpSessionHandler(mt)
        codec = AcpCodec()
        try:
            listed_before = await _request(
                dispatcher,
                codec,
                MustangMethod.MCP_LIST,
                {},
                request_id=10,
            )
            added = await _request(
                dispatcher,
                codec,
                MustangMethod.MCP_CREATE,
                {
                    "name": "added",
                    "config": {"type": "stdio", "command": "python"},
                    "expectedRevision": listed_before["result"]["revision"],
                },
                request_id=11,
            )
            updated = await _request(
                dispatcher,
                codec,
                MustangMethod.MCP_UPDATE,
                {
                    "name": "added",
                    "config": {"type": "stdio", "command": "node"},
                    "expectedRevision": added["result"]["revision"],
                },
                request_id=12,
            )
            deleted = await _request(
                dispatcher,
                codec,
                MustangMethod.MCP_DELETE,
                {"name": "added", "expectedRevision": updated["result"]["revision"]},
                request_id=13,
            )

            secret_ref = f"secret:{uuid4()}"
            plaintext = "mcp-oauth-plaintext-token"
            oauth = await _request(
                dispatcher,
                codec,
                MustangMethod.MCP_CREATE,
                {
                    "name": "remote",
                    "config": {
                        "type": "http",
                        "url": "https://mcp.example.test",
                        "headers": {"Authorization": secret_ref},
                    },
                    "expectedRevision": deleted["result"]["revision"],
                },
                request_id=14,
            )
            read_remote = await _request(
                dispatcher,
                codec,
                MustangMethod.MCP_READ,
                {"name": "remote"},
                request_id=15,
            )
        finally:
            mt.config.close()

        store = ResourceStore.open(home)
        try:
            row_payload = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT payload_json FROM config_sections WHERE file = 'mcp' AND section = 'mcp'"
                ).fetchone()[0]
            )
            export_path = home / "resource-export.json"
            store.export("json", export_path, dry_run=False)
            exported = orjson.dumps(orjson.loads(export_path.read_bytes())).decode()
        finally:
            store.close()

        oauth_secret_ref_preserved = secret_ref in row_payload
        mcp_plaintext_leaked = plaintext in row_payload or plaintext in exported
        runtime_state_not_durable = (
            "FailedServer" not in row_payload and "not found" not in row_payload
        )

        checks = {
            "mcp_startup_from_resource_store": mcp_startup_from_resource_store,
            "legacy_import_once": legacy_import_once,
            "legacy_drift_ignored": legacy_drift_ignored,
            "mcp_management_surface": read_remote["result"]["server"]["name"] == "remote",
            "revision_after_import": revision_after_import,
            "revision_after_add": added["result"]["revision"],
            "revision_after_update": updated["result"]["revision"],
            "revision_after_delete": deleted["result"]["revision"],
            "oauth_secret_ref_preserved": oauth_secret_ref_preserved,
            "mcp_plaintext_leaked": mcp_plaintext_leaked,
            "runtime_state_not_durable": runtime_state_not_durable,
            "management_response_plaintext_leaked": plaintext
            in orjson.dumps(read_remote["result"]).decode(),
            "final_revision": oauth["result"]["revision"],
        }
        print("probe=mcp_resource_store")
        for key, value in checks.items():
            print(f"{key}={value}")

        assert checks["mcp_startup_from_resource_store"] is True
        assert checks["legacy_import_once"] is True
        assert checks["legacy_drift_ignored"] is True
        assert checks["mcp_management_surface"] is True
        assert checks["revision_after_import"] == 1
        assert checks["revision_after_add"] == 2
        assert checks["revision_after_update"] == 3
        assert checks["revision_after_delete"] == 4
        assert checks["oauth_secret_ref_preserved"] is True
        assert checks["mcp_plaintext_leaked"] is False
        assert checks["runtime_state_not_durable"] is True
        assert checks["management_response_plaintext_leaked"] is False
        print("result=PASS")


if __name__ == "__main__":
    asyncio.run(_main())
