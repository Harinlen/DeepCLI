"""SecretManager — bootstrap service for credential storage.

SecretManager is constructed and started by the kernel lifespan right
after :class:`kernel.flags.FlagManager` and **before**
:class:`kernel.config.ConfigManager`.  ConfigManager may contain
``${secret:name}`` references that need SecretManager to be ready
before YAML values are expanded.

This is **not** a :class:`kernel.subsystem.Subsystem` subclass — it
is a bootstrap service with a dedicated typed slot on
:class:`kernel.module_table.KernelModuleTable`, same as FlagManager
and ConfigManager.

Security model: file permissions (0600) + LLM isolation.
No database encryption — see design doc ``docs/plans/pending/secret-manager.md`` §3.1.
"""

from __future__ import annotations

import orjson
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.secrets.types import (
    OAuthToken,
    SecretDatabaseError,
    SecretNotFoundError,
)

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".mustang" / "secrets.db"

_SECRET_RE = re.compile(r"\$\{secret:([^}]+)\}")

# Schema version — both tables created at once (no phased migration).
_SCHEMA_VERSION = 2

_SCHEMA_SQL = """\
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS secrets (
    name        TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'static',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    name            TEXT PRIMARY KEY REFERENCES secrets(name) ON DELETE CASCADE,
    refresh_token   TEXT,
    expires_at      TEXT,
    client_config   TEXT NOT NULL DEFAULT '{}',
    server_key      TEXT NOT NULL UNIQUE
);
"""


class SecretManager:
    """Bootstrap service — credential store backed by SQLite.

    Loaded before ConfigManager.  Not a Subsystem subclass (same as
    FlagManager / ConfigManager): has a dedicated typed slot on
    KernelModuleTable.

    Parameters
    ----------
    db_path:
        Override database location.  Defaults to
        ``~/.mustang/secrets.db``.  Tests pass a ``tmp_path``-based
        path to stay hermetic.
    """

    def __init__(self, *, db_path: Path | None = None) -> None:
        self._db_path: Path = db_path if db_path is not None else _DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Open (or create) the database, run migrations, enforce 0600.

        Safe to call more than once — subsequent calls are no-ops.
        """
        if self._conn is not None:
            return

        # Ensure parent directory exists with 0700.
        self._db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        # If the file doesn't exist yet, touch it with 0600 *before*
        # sqlite3.connect creates it with default umask permissions.
        if not self._db_path.exists():
            self._db_path.touch(mode=0o600)

        try:
            self._conn = sqlite3.connect(
                str(self._db_path),
                isolation_level=None,  # autocommit for PRAGMAs
            )
        except sqlite3.Error as exc:
            raise SecretDatabaseError(f"Cannot open {self._db_path}: {exc}") from exc

        self._migrate()
        self._enforce_permissions()

        logger.info("SecretManager started — db=%s", self._db_path)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, name: str) -> str | None:
        """Return the plaintext value, or ``None`` if not found."""
        row = self._execute(
            "SELECT value FROM secrets WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row else None

    def set(
        self,
        name: str,
        value: str,
        *,
        kind: str = "static",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or update a secret."""
        now = _now_iso()
        meta_json = orjson.dumps(metadata or {}).decode()
        self._execute(
            """
            INSERT INTO secrets (name, value, type, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                value = excluded.value,
                type = excluded.type,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (name, value, kind, meta_json, now, now),
        )

    def delete(self, name: str) -> bool:
        """Delete a secret.  Returns ``True`` if it existed.

        CASCADE deletes the matching ``oauth_tokens`` row if present.
        """
        cur = self._execute("DELETE FROM secrets WHERE name = ?", (name,))
        return cur.rowcount > 0

    def list_names(self, *, kind: str | None = None) -> list[str]:
        """Return secret names, optionally filtered by kind."""
        if kind is not None:
            rows = self._execute(
                "SELECT name FROM secrets WHERE type = ? ORDER BY name",
                (kind,),
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT name FROM secrets ORDER BY name"
            ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # ${secret:name} expansion
    # ------------------------------------------------------------------

    def resolve(self, template: str) -> str:
        """Expand ``${secret:name}`` references in *template*.

        Raises :class:`SecretNotFoundError` for unknown names (fail
        loud — unlike ``$VAR`` env expansion which silently empties).
        """
        def _replace(m: re.Match[str]) -> str:
            secret_name = m.group(1)
            value = self.get(secret_name)
            if value is None:
                raise SecretNotFoundError(
                    f"Secret {secret_name!r} referenced in config but not found "
                    f"in {self._db_path}. Use '/auth set {secret_name} <value>' "
                    f"to store it."
                )
            return value

        return _SECRET_RE.sub(_replace, template)

    # ------------------------------------------------------------------
    # OAuth convenience (thin wrappers over CRUD)
    # ------------------------------------------------------------------

    def get_oauth_token(self, server_key: str) -> OAuthToken | None:
        """Return the full OAuth token bundle for an MCP server.

        Looks up secrets row ``oauth:<server_key>`` + oauth_tokens row.
        Returns ``None`` if no token stored for this server.
        """
        row = self._execute(
            """
            SELECT s.value, o.refresh_token, o.expires_at, o.client_config
            FROM secrets s
            JOIN oauth_tokens o ON s.name = o.name
            WHERE o.server_key = ?
            """,
            (server_key,),
        ).fetchone()
        if row is None:
            return None
        access_token, refresh_token, expires_at_str, client_config_json = row
        return OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=(
                datetime.fromisoformat(expires_at_str)
                if expires_at_str
                else None
            ),
            client_config=orjson.loads(client_config_json) if client_config_json else {},
        )

    def set_oauth_token(self, server_key: str, token: OAuthToken) -> None:
        """Persist an OAuth token bundle (access + refresh + expiry).

        Upserts secrets row with ``name='oauth:<server_key>'``,
        ``type='oauth'``, ``value=access_token``.  Then upserts the
        ``oauth_tokens`` row with refresh/expiry/client_config.
        """
        name = f"oauth:{server_key}"
        now = _now_iso()

        # Upsert secrets row.
        self._execute(
            """
            INSERT INTO secrets (name, value, type, metadata, created_at, updated_at)
            VALUES (?, ?, 'oauth', '{}', ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (name, token.access_token, now, now),
        )

        # Upsert oauth_tokens row.
        expires_at_str = token.expires_at.isoformat() if token.expires_at else None
        client_config_json = orjson.dumps(token.client_config).decode()
        self._execute(
            """
            INSERT INTO oauth_tokens (name, refresh_token, expires_at, client_config, server_key)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at,
                client_config = excluded.client_config
            """,
            (name, token.refresh_token, expires_at_str, client_config_json, server_key),
        )

    def delete_oauth_token(self, server_key: str) -> bool:
        """Delete OAuth token for a server.  CASCADE cleans oauth_tokens."""
        name = f"oauth:{server_key}"
        return self.delete(name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> sqlite3.Cursor:
        """Execute SQL, wrapping errors in :class:`SecretDatabaseError`."""
        assert self._conn is not None, "SecretManager not started"
        try:
            return self._conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise SecretDatabaseError(str(exc)) from exc

    def _migrate(self) -> None:
        """Create tables / run migrations based on ``user_version``."""
        assert self._conn is not None
        try:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as exc:
            raise SecretDatabaseError(
                f"Cannot read database version — file may be corrupt or "
                f"encrypted with a different tool: {exc}"
            ) from exc

        if version >= _SCHEMA_VERSION:
            return  # already up to date

        try:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise SecretDatabaseError(f"Schema migration failed: {exc}") from exc

        logger.info(
            "SecretManager migrated schema %d → %d", version, _SCHEMA_VERSION
        )

    def _enforce_permissions(self) -> None:
        """Ensure the DB file has 0600 permissions (best-effort)."""
        try:
            current_mode = self._db_path.stat().st_mode & 0o777
            if current_mode != 0o600:
                self._db_path.chmod(0o600)
                logger.warning(
                    "secrets.db permissions were %04o, fixed to 0600",
                    current_mode,
                )
        except OSError:
            # Windows or other platforms where chmod is a no-op.
            pass


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
