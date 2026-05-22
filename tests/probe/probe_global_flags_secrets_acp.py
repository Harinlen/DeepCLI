from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.agents.access.security.context import AuthContext
from kernel.core.flags import FlagManager
from kernel.core.protocol.acp.codec import AcpCodec
from kernel.core.protocol.acp.namespaces import MustangMethod
from kernel.core.protocol.acp.session_handler import AcpSessionHandler
from kernel.core.secrets import SecretManager
from kernel.core.storage import ResourceStore


class _ModuleTable:
    def __init__(self, *, home: Path, flags: FlagManager, secrets: SecretManager) -> None:
        self.state_dir = home / "state"
        self.state_dir.mkdir()
        self.flags = flags
        self.secrets = secrets


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
    auth = _auth(f"probe-management-{request_id}")
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
    with tempfile.TemporaryDirectory(prefix="mustang-management-probe-") as raw_home:
        home = Path(raw_home)
        flags = FlagManager(resource_home=home)
        await flags.initialize()
        secrets = SecretManager(db_path=home / "secrets.db")
        await secrets.startup()
        store = ResourceStore.open(home)
        try:
            store.cas_put_resource("probe.resource", '{"value":1}', actor="primary")
        finally:
            store.close()

        dispatcher = AcpSessionHandler(_ModuleTable(home=home, flags=flags, secrets=secrets))
        codec = AcpCodec()
        try:
            export_path = home / "global-export.json"
            export = await _request(
                dispatcher,
                codec,
                MustangMethod.GLOBAL_EXPORT,
                {"outputPath": str(export_path), "dryRun": False},
                request_id=1,
            )
            dry_run = await _request(
                dispatcher,
                codec,
                MustangMethod.GLOBAL_IMPORT,
                {"inputPath": str(export_path), "dryRun": True},
                request_id=2,
            )
            apply = await _request(
                dispatcher,
                codec,
                MustangMethod.GLOBAL_IMPORT,
                {"inputPath": str(export_path), "dryRun": False},
                request_id=3,
            )

            before = flags.get_section("kernel").memory
            flag_write = await _request(
                dispatcher,
                codec,
                MustangMethod.FLAGS_SET,
                {"section": "kernel", "key": "memory", "value": not before},
                request_id=4,
            )
            frozen_snapshot = flags.get_section("kernel").memory is before

            ref = secrets.create("probe-api-key", b"sk-probe-hidden", actor="primary")
            denied = await _request(
                dispatcher,
                codec,
                MustangMethod.SECRETS_LIST,
                {"actorAgentId": "ordinary"},
                request_id=5,
            )
            renamed = await _request(
                dispatcher,
                codec,
                MustangMethod.SECRETS_RENAME,
                {
                    "secretId": ref.secret_id,
                    "name": "renamed-probe-api-key",
                    "expectedRevision": ref.revision,
                },
                request_id=6,
            )
            audit = await _request(
                dispatcher,
                codec,
                MustangMethod.SECRETS_AUDIT,
                {},
                request_id=7,
            )
            await _request(
                dispatcher,
                codec,
                MustangMethod.SECRETS_DELETE,
                {
                    "secretId": ref.secret_id,
                    "expectedRevision": renamed["result"]["revision"],
                    "confirm": True,
                },
                request_id=8,
            )

            exported_text = export_path.read_text(encoding="utf-8")
            secret_payload = json.dumps([denied, renamed, audit, export], sort_keys=True)
            plaintext_leaked = (
                "sk-probe-hidden" in exported_text or "sk-probe-hidden" in secret_payload
            )

            checks = {
                "global_export_count": export["result"]["resourceCount"],
                "global_import_dry_run": dry_run["result"]["dryRun"],
                "global_import_apply_unavailable": apply["result"]["unavailable"],
                "flags_pending_restart": flag_write["result"]["pendingRestart"],
                "flags_frozen_snapshot": frozen_snapshot,
                "ordinary_actor_denied": "error" in denied,
                "secret_rename_stable_ref": renamed["result"]["ref"] == ref.ref,
                "secrets_plaintext_leaked": plaintext_leaked,
            }
            for key, value in checks.items():
                print(f"{key}={value}")

            assert checks["global_export_count"] == 1
            assert checks["global_import_dry_run"] is True
            assert checks["global_import_apply_unavailable"] is True
            assert checks["flags_pending_restart"] is True
            assert checks["flags_frozen_snapshot"] is True
            assert checks["ordinary_actor_denied"] is True
            assert checks["secret_rename_stable_ref"] is True
            assert checks["secrets_plaintext_leaked"] is False
            print("probe=global_flags_secrets_acp result=PASS")
        finally:
            flags.close()
            secrets.close()


if __name__ == "__main__":
    asyncio.run(_main())
