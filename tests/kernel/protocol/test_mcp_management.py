from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from kernel.agents.access.security.context import AuthContext
from kernel.core.config import ConfigManager
from kernel.core.protocol.acp.codec import AcpCodec
from kernel.core.protocol.acp.namespaces import MustangMethod
from kernel.core.protocol.acp.routing import REQUEST_DISPATCH
from kernel.core.protocol.acp.session_handler import AcpSessionHandler
from kernel.core.storage import ResourceStore


def _auth(connection_id: str) -> AuthContext:
    return AuthContext(
        connection_id=connection_id,
        credential_type="token",
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )


class _ModuleTable:
    def __init__(self, *, home: Path, config: ConfigManager) -> None:
        self.state_dir = home / "state"
        self.state_dir.mkdir(mode=0o700)
        self.config = config


async def _request(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    method: str,
    params: dict[str, Any],
    *,
    request_id: int,
) -> dict[str, Any]:
    auth = _auth(f"mcp-management-{request_id}")
    init = codec.decode(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "test", "title": "Test"},
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


def test_mcp_methods_are_routable() -> None:
    for method in (
        MustangMethod.MCP_LIST,
        MustangMethod.MCP_READ,
        MustangMethod.MCP_CREATE,
        MustangMethod.MCP_UPDATE,
        MustangMethod.MCP_DELETE,
    ):
        assert method in REQUEST_DISPATCH


@pytest.mark.anyio
async def test_acp_mcp_management_crud_revision_and_secret_redaction(tmp_path: Path) -> None:
    config = ConfigManager(resource_home=tmp_path)
    await config.startup()
    dispatcher = AcpSessionHandler(_ModuleTable(home=tmp_path, config=config))
    codec = AcpCodec()
    secret_ref = f"secret:{uuid4()}"
    plaintext = "mcp-plaintext-token"
    try:
        denied = await _request(
            dispatcher,
            codec,
            MustangMethod.MCP_LIST,
            {"actorAgentId": "ordinary"},
            request_id=1,
        )
        assert denied["error"]["code"] == -32602

        created = await _request(
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
            },
            request_id=2,
        )
        assert created["result"]["revision"] == 1
        assert created["result"]["pendingRestart"] is True
        assert created["result"]["server"]["config"]["headers"]["Authorization"] == secret_ref

        duplicate = await _request(
            dispatcher,
            codec,
            MustangMethod.MCP_CREATE,
            {"name": "remote", "config": {"type": "http", "url": "https://mcp.example.test"}},
            request_id=3,
        )
        assert duplicate["error"]["code"] == -32602

        rejected_plaintext = await _request(
            dispatcher,
            codec,
            MustangMethod.MCP_UPDATE,
            {
                "name": "remote",
                "config": {
                    "type": "http",
                    "url": "https://mcp.example.test",
                    "headers": {"Authorization": plaintext},
                },
                "expectedRevision": 1,
            },
            request_id=4,
        )
        assert rejected_plaintext["error"]["code"] == -32602

        updated = await _request(
            dispatcher,
            codec,
            MustangMethod.MCP_UPDATE,
            {
                "name": "remote",
                "config": {
                    "type": "http",
                    "url": "https://mcp.example.test/v2",
                    "headers": {"Authorization": secret_ref},
                },
                "expectedRevision": 1,
            },
            request_id=5,
        )
        assert updated["result"]["revision"] == 2

        listed = await _request(
            dispatcher,
            codec,
            MustangMethod.MCP_LIST,
            {},
            request_id=6,
        )
        assert listed["result"]["servers"][0]["name"] == "remote"
        assert plaintext not in json.dumps(listed)

        deleted = await _request(
            dispatcher,
            codec,
            MustangMethod.MCP_DELETE,
            {"name": "remote", "expectedRevision": 2},
            request_id=7,
        )
        assert deleted["result"]["deleted"] is True
        assert deleted["result"]["revision"] == 3

        store = ResourceStore.open(tmp_path)
        try:
            row = store.read_tx(
                lambda conn: conn.execute(
                    "SELECT revision, payload_json FROM config_sections "
                    "WHERE file = 'mcp' AND section = 'mcp'"
                ).fetchone()
            )
        finally:
            store.close()
        assert row[0] == 3
        assert plaintext not in row[1]
    finally:
        config.close()
