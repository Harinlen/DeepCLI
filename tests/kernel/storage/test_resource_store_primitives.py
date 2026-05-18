from __future__ import annotations

import sqlite3

import pytest

from kernel.core.storage import ResourceStore, RevisionConflict


def test_cas_success_increments_revision(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        created = store.cas_put_resource("test.resource", '{"value":1}', actor="primary")
        updated = store.cas_put_resource(
            "test.resource",
            '{"value":2}',
            expected_revision=created.revision,
            actor="primary",
        )

        assert created.revision == 1
        assert updated.revision == 2
        assert store.current_revisions("test.") == {"test.resource": 2}
        assert store.get_resource("test.resource") == updated
    finally:
        store.close()


def test_cas_conflict_returns_current_revision_and_hash(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        current = store.cas_put_resource("test.resource", '{"value":1}', actor="primary")

        with pytest.raises(RevisionConflict) as exc_info:
            store.cas_put_resource(
                "test.resource",
                '{"value":2}',
                expected_revision=99,
                actor="primary",
            )

        assert exc_info.value.current_revision == current.revision
        assert exc_info.value.current_hash == current.payload_hash
    finally:
        store.close()


def test_blind_create_conflict_is_explicit(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        current = store.cas_put_resource("test.resource", '{"value":1}', actor="primary")

        with pytest.raises(RevisionConflict, match="already exists") as exc_info:
            store.cas_put_resource("test.resource", '{"value":2}', actor="primary")

        assert exc_info.value.current_revision == current.revision
        assert exc_info.value.current_hash == current.payload_hash
    finally:
        store.close()


def test_event_and_payload_commit_atomically(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="after resource"):
            store.write_tx(lambda conn: _write_resource_without_event(conn))

        assert store.get_resource("test.atomic") is None
        event_count = store.read_tx(
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM global_resource_events WHERE resource_key = ?",
                ("test.atomic",),
            ).fetchone()[0]
        )
        assert event_count == 0
    finally:
        store.close()


def test_backup_db_opens_and_has_same_revision_state(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        store.cas_put_resource("test.resource", '{"value":1}', actor="primary")
        backup = store.backup(tmp_path / "backups" / "global.db")
    finally:
        store.close()

    backup_store = ResourceStore.open(tmp_path / "backups", apply_migrations=False)
    try:
        assert backup.source_schema_version == 6
        assert len(backup.checksum) == 64
        assert backup_store.current_revisions() == {"test.resource": 1}
    finally:
        backup_store.close()


def test_export_dry_run_reports_json_shape_without_writing(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    output = tmp_path / "export.json"
    try:
        store.cas_put_resource("test.resource", '{"value":1}', actor="primary")
        report = store.export("json", output, dry_run=True)

        assert report.dry_run is True
        assert report.format == "json"
        assert report.resource_count == 1
        assert report.event_count == 1
        assert not output.exists()
    finally:
        store.close()


def _write_resource_without_event(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO global_resources (
            resource_key, payload_json, revision, updated_at,
            updated_by_agent_id, payload_hash
        )
        VALUES ('test.atomic', '{}', 1, '2026-05-17T00:00:00Z', 'test', 'hash')
        """
    )
    raise RuntimeError("after resource")
