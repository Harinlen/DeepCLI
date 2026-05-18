from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from kernel.core.storage import ResourceStore, SecretStore
from kernel.core.storage.global_commands import (
    GlobalAuthorizationError,
    GlobalResourceCommandService,
    GlobalRestoreUnavailable,
    read_sqlite_user_version,
)


def test_primary_can_run_backup_and_export(tmp_path: Path) -> None:
    _seed_resource(tmp_path)
    service = GlobalResourceCommandService(tmp_path)

    backup = service.backup(actor_agent_id="primary")
    export = service.export(
        actor_agent_id="primary",
        output_path=tmp_path / "exports" / "global.json",
        dry_run=False,
    )
    backups = service.backups(actor_agent_id="primary")

    assert Path(backup.path).exists()
    assert read_sqlite_user_version(Path(backup.path)) == backup.source_schema_version == 6
    assert export.resource_count == 1
    assert export.output_path is not None and Path(export.output_path).exists()
    assert backup.path in backups.backups


def test_ordinary_agent_denied(tmp_path: Path) -> None:
    service = GlobalResourceCommandService(tmp_path)

    with pytest.raises(GlobalAuthorizationError):
        service.backup(actor_agent_id="ordinary")
    with pytest.raises(GlobalAuthorizationError):
        service.export(actor_agent_id="ordinary")


def test_export_omits_secret_plaintext(tmp_path: Path) -> None:
    _seed_resource(tmp_path)
    secrets = SecretStore.open(tmp_path)
    try:
        secrets.cas_secret(
            secret_id="secret-api",
            name="api",
            encrypted_payload=b"super-secret-ciphertext",
            expected_revision=None,
            actor="primary",
        )
    finally:
        secrets.close()

    service = GlobalResourceCommandService(tmp_path)
    export = service.export(
        actor_agent_id="primary",
        output_path=tmp_path / "exports" / "global.json",
        dry_run=False,
    )

    payload = Path(export.output_path or "").read_text()
    assert "super-secret-ciphertext" not in payload
    assert "secrets" not in payload


def test_import_dry_run_reports_conflicts_without_mutation(tmp_path: Path) -> None:
    _seed_resource(tmp_path)
    service = GlobalResourceCommandService(tmp_path)
    export_path = tmp_path / "exports" / "global.json"
    service.export(actor_agent_id="primary", output_path=export_path, dry_run=False)
    payload = orjson.loads(export_path.read_bytes())
    payload["resources"][0]["payload_hash"] = "changed"
    import_path = tmp_path / "exports" / "changed.json"
    import_path.write_bytes(orjson.dumps(payload))

    report = service.import_dry_run(actor_agent_id="primary", input_path=import_path)

    assert report.dry_run is True
    assert report.conflicts == ("test.resource",)
    store = ResourceStore.open(tmp_path)
    try:
        resource = store.get_resource("test.resource")
        assert resource is not None
        assert resource.payload_json == '{"value": 1}'
    finally:
        store.close()


def test_import_apply_is_unavailable(tmp_path: Path) -> None:
    _seed_resource(tmp_path)
    service = GlobalResourceCommandService(tmp_path)
    export_path = tmp_path / "exports" / "global.json"
    service.export(actor_agent_id="primary", output_path=export_path, dry_run=False)

    with pytest.raises(GlobalRestoreUnavailable):
        service.import_apply(actor_agent_id="primary", input_path=export_path)


def _seed_resource(tmp_path: Path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        store.cas_put_resource("test.resource", '{"value": 1}', actor="primary")
    finally:
        store.close()
