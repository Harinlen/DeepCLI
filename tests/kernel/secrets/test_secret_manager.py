"""Unit tests for SecretManager — CRUD, resolve, OAuth, permissions."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from kernel.core.secrets import SecretManager
from kernel.core.secrets.types import OAuthToken, SecretNotFoundError
from kernel.core.config.sqlite_backend import ConfigSQLiteBackend
from kernel.core.storage import ResourceStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def sm(tmp_path):
    """A fresh SecretManager rooted in tmp_path."""
    mgr = SecretManager(db_path=tmp_path / "secrets.db")
    await mgr.startup()
    yield mgr
    mgr.close()


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_get_roundtrip(sm: SecretManager):
    sm.set("api-key", "sk-12345")
    assert sm.get("api-key") == "sk-12345"


@pytest.mark.anyio
async def test_get_missing_returns_none(sm: SecretManager):
    assert sm.get("nonexistent") is None


@pytest.mark.anyio
async def test_upsert_overwrites(sm: SecretManager):
    sm.set("key", "v1")
    sm.set("key", "v2")
    assert sm.get("key") == "v2"


@pytest.mark.anyio
async def test_delete_existing(sm: SecretManager):
    sm.set("key", "val")
    assert sm.delete("key") is True
    assert sm.get("key") is None


@pytest.mark.anyio
async def test_delete_missing(sm: SecretManager):
    assert sm.delete("nonexistent") is False


@pytest.mark.anyio
async def test_list_names_all(sm: SecretManager):
    sm.set("b", "1")
    sm.set("a", "2")
    assert sm.list_names() == ["a", "b"]  # sorted


@pytest.mark.anyio
async def test_list_names_filtered(sm: SecretManager):
    sm.set("key1", "v", kind="static")
    sm.set("key2", "v", kind="bearer")
    sm.set("key3", "v", kind="static")
    assert sm.list_names(kind="static") == ["key1", "key3"]
    assert sm.list_names(kind="bearer") == ["key2"]
    assert sm.list_names(kind="oauth") == []


@pytest.mark.anyio
async def test_set_with_metadata(sm: SecretManager):
    sm.set("key", "val", metadata={"source": "cli"})
    assert sm.get("key") == "val"


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_expands(sm: SecretManager):
    sm.set("token", "abc123")
    assert sm.resolve("Bearer ${secret:token}") == "Bearer abc123"


@pytest.mark.anyio
async def test_resolve_multiple(sm: SecretManager):
    sm.set("a", "X")
    sm.set("b", "Y")
    assert sm.resolve("${secret:a}-${secret:b}") == "X-Y"


@pytest.mark.anyio
async def test_resolve_missing_raises(sm: SecretManager):
    with pytest.raises(SecretNotFoundError, match="nonexistent"):
        sm.resolve("${secret:nonexistent}")


@pytest.mark.anyio
async def test_resolve_no_pattern_passthrough(sm: SecretManager):
    assert sm.resolve("plain string") == "plain string"
    assert sm.resolve("$OTHER_VAR") == "$OTHER_VAR"
    assert sm.resolve("${OTHER_VAR}") == "${OTHER_VAR}"


# ---------------------------------------------------------------------------
# OAuth token convenience
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_oauth_token_roundtrip(sm: SecretManager):
    token = OAuthToken(
        access_token="at_123",
        refresh_token="rt_456",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        client_config={"client_id": "cid", "token_endpoint": "https://example.com/token"},
    )
    sm.set_oauth_token("github-mcp", token)

    result = sm.get_oauth_token("github-mcp")
    assert result is not None
    assert result.access_token == "at_123"
    assert result.refresh_token == "rt_456"
    assert result.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert result.client_config["client_id"] == "cid"


@pytest.mark.anyio
async def test_oauth_token_upsert(sm: SecretManager):
    t1 = OAuthToken(access_token="old")
    t2 = OAuthToken(access_token="new")
    sm.set_oauth_token("srv", t1)
    sm.set_oauth_token("srv", t2)
    assert sm.get_oauth_token("srv").access_token == "new"


@pytest.mark.anyio
async def test_oauth_get_missing(sm: SecretManager):
    assert sm.get_oauth_token("nonexistent") is None


@pytest.mark.anyio
async def test_oauth_delete(sm: SecretManager):
    sm.set_oauth_token("srv", OAuthToken(access_token="at"))
    assert sm.delete_oauth_token("srv") is True
    assert sm.get_oauth_token("srv") is None


@pytest.mark.anyio
async def test_oauth_delete_cascades(sm: SecretManager):
    """Deleting the secrets row cascades to oauth_tokens."""
    sm.set_oauth_token("srv", OAuthToken(access_token="at"))
    # Delete via the base CRUD (not the OAuth convenience).
    sm.delete("oauth:srv")
    assert sm.get_oauth_token("srv") is None


@pytest.mark.anyio
async def test_oauth_appears_in_list(sm: SecretManager):
    sm.set_oauth_token("srv", OAuthToken(access_token="at"))
    assert "oauth:srv" in sm.list_names(kind="oauth")


@pytest.mark.anyio
async def test_oauth_no_refresh_token(sm: SecretManager):
    """OAuth token without refresh_token or expires_at."""
    sm.set_oauth_token("srv", OAuthToken(access_token="at"))
    result = sm.get_oauth_token("srv")
    assert result.refresh_token is None
    assert result.expires_at is None


# ---------------------------------------------------------------------------
# File permissions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_file_permissions(tmp_path):
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    mode = (tmp_path / "secrets.db").stat().st_mode & 0o777
    assert mode == 0o600
    sm.close()


@pytest.mark.anyio
async def test_permissions_auto_repair(tmp_path):
    db = tmp_path / "secrets.db"
    sm = SecretManager(db_path=db)
    await sm.startup()
    sm.close()

    db.chmod(0o644)

    sm2 = SecretManager(db_path=db)
    await sm2.startup()
    assert db.stat().st_mode & 0o777 == 0o600
    sm2.close()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_data_persists_across_restarts(tmp_path):
    db_path = tmp_path / "secrets.db"

    sm1 = SecretManager(db_path=db_path)
    await sm1.startup()
    sm1.set("key", "persistent-value")
    sm1.close()

    sm2 = SecretManager(db_path=db_path)
    await sm2.startup()
    assert sm2.get("key") == "persistent-value"
    sm2.close()


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schema_version(sm: SecretManager):
    """Database has the expected user_version after startup."""
    row = sm._conn.execute("PRAGMA user_version").fetchone()
    assert row[0] == 2


# ---------------------------------------------------------------------------
# SQLite binary format
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_file_is_sqlite_binary(tmp_path):
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    sm.set("password", "super-secret-value-12345")
    sm.close()

    raw = (tmp_path / "secrets.db").read_bytes()
    assert raw[:6] == b"SQLite"


# ---------------------------------------------------------------------------
# Idempotent startup
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_startup_idempotent(tmp_path):
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    await sm.startup()  # second call is a no-op
    sm.set("key", "val")
    assert sm.get("key") == "val"
    sm.close()


@pytest.mark.anyio
async def test_uuid_ref_rename_does_not_rewrite_config_or_export_plaintext(tmp_path):
    sm = SecretManager(db_path=tmp_path / "secrets.db")
    await sm.startup()
    try:
        ref = sm.create("api-key", b"sk-production", actor="primary")
        store = ResourceStore.open(tmp_path)
        try:
            config = ConfigSQLiteBackend(store).write(
                file="config",
                section="provider",
                payload={"api_key": ref.ref},
                expected_revision=None,
                actor="primary",
            )
            sm.rename(
                ref.secret_id,
                "renamed-key",
                expected_revision=ref.revision,
                actor="primary",
            )
            after = ConfigSQLiteBackend(store).read(file="config", section="provider")
            export = store.export("json", dry_run=False)
        finally:
            store.close()

        assert after is not None
        assert after.revision == config.revision
        assert after.payload["api_key"] == ref.ref
        assert sm.resolve_id(ref.secret_id) == b"sk-production"
        assert sm.resolve(ref.ref) == "sk-production"
        assert "sk-production" not in str(export)
    finally:
        sm.close()


@pytest.mark.anyio
async def test_legacy_name_based_db_imports_to_uuid_store(tmp_path):
    db = tmp_path / "secrets.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE secrets (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'static',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE oauth_tokens (
                name TEXT PRIMARY KEY REFERENCES secrets(name) ON DELETE CASCADE,
                refresh_token TEXT,
                expires_at TEXT,
                client_config TEXT NOT NULL DEFAULT '{}',
                server_key TEXT NOT NULL UNIQUE
            );
            PRAGMA user_version = 2;
            """
        )
        conn.execute(
            """
            INSERT INTO secrets (name, value, type, metadata, created_at, updated_at)
            VALUES ('api-key', 'sk-old', 'static', '{}', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO secrets (name, value, type, metadata, created_at, updated_at)
            VALUES ('oauth:srv', 'at-old', 'oauth', '{}', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO oauth_tokens
                (name, refresh_token, expires_at, client_config, server_key)
            VALUES
                ('oauth:srv', 'rt-old', NULL, '{"client_id":"cid"}', 'srv')
            """
        )
        conn.commit()
    finally:
        conn.close()

    sm = SecretManager(db_path=db)
    await sm.startup()
    try:
        assert sm.get("api-key") == "sk-old"
        token = sm.get_oauth_token("srv")
        assert token is not None
        assert token.access_token == "at-old"
        assert token.refresh_token == "rt-old"
        assert token.client_config["client_id"] == "cid"
        assert sm.list()[0].secret_id
    finally:
        sm.close()

    assert list(tmp_path.glob("secrets.db.legacy-*"))
