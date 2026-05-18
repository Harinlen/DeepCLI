from __future__ import annotations

import sqlite3

import pytest

from kernel.core.storage import ResourceStore, SecretStore, StoreMigrationError, StoreOpenError


@pytest.mark.parametrize(
    ("store_cls", "table_name", "schema_version"),
    [
        (ResourceStore, "global_resources", 6),
        (SecretStore, "secrets", 2),
    ],
)
def test_open_new_db_stamps_schema_version(tmp_path, store_cls, table_name, schema_version) -> None:
    store = store_cls.open(tmp_path)
    try:
        assert store.schema_version == schema_version
        tables = store.read_tx(
            lambda conn: {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        )
        assert table_name in tables
    finally:
        store.close()


@pytest.mark.parametrize("store_cls", [ResourceStore, SecretStore])
def test_rejects_future_schema_version(tmp_path, store_cls) -> None:
    db_path = tmp_path / store_cls.db_name
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA user_version = 999")
    finally:
        conn.close()

    with pytest.raises(StoreMigrationError, match="newer than this build"):
        store_cls.open(tmp_path)


@pytest.mark.parametrize("store_cls", [ResourceStore, SecretStore])
def test_sets_wal_and_foreign_keys(tmp_path, store_cls) -> None:
    store = store_cls.open(tmp_path)
    try:
        assert store.journal_mode.lower() == "wal"
        foreign_keys = store.read_tx(
            lambda conn: int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        )
        busy_timeout = store.read_tx(
            lambda conn: int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        )
        assert foreign_keys == 1
        assert busy_timeout == 5000
    finally:
        store.close()


def test_resource_store_rolls_back_failed_write_transaction(tmp_path) -> None:
    store = ResourceStore.open(tmp_path)
    try:
        with pytest.raises(ValueError, match="boom"):
            store.write_tx(_insert_then_fail)

        count = store.read_tx(
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM global_resources WHERE resource_key = 'probe.rollback'"
            ).fetchone()[0]
        )
        assert count == 0
    finally:
        store.close()


def test_secret_store_rolls_back_failed_write_transaction(tmp_path) -> None:
    store = SecretStore.open(tmp_path)
    try:
        with pytest.raises(ValueError, match="boom"):
            store.write_tx(_insert_secret_then_fail)

        count = store.read_tx(
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM secrets WHERE secret_id = 'secret-1'"
            ).fetchone()[0]
        )
        assert count == 0
    finally:
        store.close()


@pytest.mark.parametrize("store_cls", [ResourceStore, SecretStore])
def test_closes_cleanly(tmp_path, store_cls) -> None:
    store = store_cls.open(tmp_path)
    store.close()
    store.close()

    with pytest.raises(StoreOpenError, match="closed"):
        store.read_tx(lambda conn: conn.execute("SELECT 1").fetchone())


def _insert_then_fail(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO global_resources (
            resource_key, payload_json, revision, updated_at,
            updated_by_agent_id, payload_hash
        )
        VALUES ('probe.rollback', '{}', 1, '2026-05-17T00:00:00Z', 'test', 'hash')
        """
    )
    raise ValueError("boom")


def _insert_secret_then_fail(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO secrets (
            secret_id, name, value_ciphertext, revision, created_at, updated_at,
            created_by_agent_id, updated_by_agent_id, payload_hash
        )
        VALUES (
            'secret-1', 'test', X'00', 1, '2026-05-17T00:00:00Z',
            '2026-05-17T00:00:00Z', 'test', 'test', 'hash'
        )
        """
    )
    raise ValueError("boom")
