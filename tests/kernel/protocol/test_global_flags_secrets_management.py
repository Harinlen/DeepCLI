from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kernel.agents.access.security.context import AuthContext
from kernel.core.flags import FlagManager
from kernel.core.protocol.acp.codec import AcpCodec
from kernel.core.protocol.acp.namespaces import MustangMethod
from kernel.core.protocol.acp.session_handler import AcpSessionHandler
from kernel.core.secrets import SecretManager
from kernel.core.storage import ResourceStore


def _auth(conn_id: str = "management-test") -> AuthContext:
    return AuthContext(
        connection_id=conn_id,
        credential_type="token",
        remote_addr="127.0.0.1:1",
        authenticated_at=datetime.now(timezone.utc),
    )


class _ModuleTable:
    def __init__(self, *, home: Path, flags: FlagManager, secrets: SecretManager) -> None:
        self.state_dir = home / "state"
        self.state_dir.mkdir()
        self.flags = flags
        self.secrets = secrets


async def _request(
    dispatcher: AcpSessionHandler,
    codec: AcpCodec,
    method: str,
    params: dict[str, Any],
    *,
    request_id: int = 1,
) -> dict[str, Any]:
    auth = _auth(f"management-test-{request_id}")
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


@pytest.mark.anyio
async def test_acp_global_backup_export_import_dry_run(tmp_path: Path) -> None:
    flags = FlagManager(resource_home=tmp_path)
    await flags.initialize()
    secrets = SecretManager(db_path=tmp_path / "secrets.db")
    await secrets.startup()
    store = ResourceStore.open(tmp_path)
    try:
        store.cas_put_resource("test.resource", '{"value":1}', actor="primary")
    finally:
        store.close()
    dispatcher = AcpSessionHandler(_ModuleTable(home=tmp_path, flags=flags, secrets=secrets))
    codec = AcpCodec()
    try:
        backup = await _request(dispatcher, codec, MustangMethod.GLOBAL_BACKUP, {})
        assert Path(backup["result"]["path"]).exists()

        export_path = tmp_path / "exports" / "global.json"
        export = await _request(
            dispatcher,
            codec,
            MustangMethod.GLOBAL_EXPORT,
            {"outputPath": str(export_path), "dryRun": False},
            request_id=2,
        )
        assert export["result"]["resourceCount"] == 1
        assert export_path.exists()

        dry_run = await _request(
            dispatcher,
            codec,
            MustangMethod.GLOBAL_IMPORT,
            {"inputPath": str(export_path), "dryRun": True},
            request_id=3,
        )
        assert dry_run["result"]["dryRun"] is True

        apply = await _request(
            dispatcher,
            codec,
            MustangMethod.GLOBAL_IMPORT,
            {"inputPath": str(export_path), "dryRun": False},
            request_id=4,
        )
        assert apply["result"]["unavailable"] is True
    finally:
        flags.close()
        secrets.close()


@pytest.mark.anyio
async def test_acp_flags_set_writes_db_without_hot_reload(tmp_path: Path) -> None:
    flags = FlagManager(resource_home=tmp_path)
    await flags.initialize()
    secrets = SecretManager(db_path=tmp_path / "secrets.db")
    await secrets.startup()
    dispatcher = AcpSessionHandler(_ModuleTable(home=tmp_path, flags=flags, secrets=secrets))
    codec = AcpCodec()
    try:
        before = flags.get_section("kernel").memory
        result = await _request(
            dispatcher,
            codec,
            MustangMethod.FLAGS_SET,
            {
                "section": "kernel",
                "key": "memory",
                "value": not before,
            },
        )
        assert result["result"]["applies"] == "after_restart"
        assert result["result"]["pendingRestart"] is True
        assert flags.get_section("kernel").memory is before

        store = ResourceStore.open(tmp_path)
        try:
            assert store.current_revisions("flags.")["flags.kernel"] == 1
            events = store.read_tx(
                lambda conn: conn.execute("SELECT COUNT(*) FROM flag_events").fetchone()[0]
            )
        finally:
            store.close()
        assert events == 1
    finally:
        flags.close()
        secrets.close()


@pytest.mark.anyio
async def test_acp_secrets_metadata_rename_delete_and_primary_guard(tmp_path: Path) -> None:
    flags = FlagManager(resource_home=tmp_path)
    await flags.initialize()
    secrets = SecretManager(db_path=tmp_path / "secrets.db")
    await secrets.startup()
    ref = secrets.create("api-key", b"sk-hidden", actor="primary")
    dispatcher = AcpSessionHandler(_ModuleTable(home=tmp_path, flags=flags, secrets=secrets))
    codec = AcpCodec()
    try:
        denied = await _request(
            dispatcher,
            codec,
            MustangMethod.SECRETS_LIST,
            {"actorAgentId": "ordinary"},
        )
        assert denied["error"]["code"] == -32602

        listed = await _request(dispatcher, codec, MustangMethod.SECRETS_LIST, {}, request_id=2)
        assert listed["result"]["secrets"][0]["name"] == "api-key"
        assert "sk-hidden" not in json.dumps(listed)

        renamed = await _request(
            dispatcher,
            codec,
            MustangMethod.SECRETS_RENAME,
            {
                "secretId": ref.secret_id,
                "name": "renamed",
                "expectedRevision": ref.revision,
            },
            request_id=3,
        )
        assert renamed["result"]["ref"] == ref.ref

        audit = await _request(dispatcher, codec, MustangMethod.SECRETS_AUDIT, {}, request_id=4)
        assert "secret.rename" in {event["eventType"] for event in audit["result"]["events"]}
        assert "sk-hidden" not in json.dumps(audit)

        deleted = await _request(
            dispatcher,
            codec,
            MustangMethod.SECRETS_DELETE,
            {
                "secretId": ref.secret_id,
                "expectedRevision": renamed["result"]["revision"],
                "confirm": True,
            },
            request_id=5,
        )
        assert deleted["result"]["deleted"] is True
    finally:
        flags.close()
        secrets.close()
