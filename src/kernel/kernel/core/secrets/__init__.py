"""SecretManager bootstrap service backed by shared SecretStore.

Production secret truth lives in ``SecretStore`` UUID rows.  The manager keeps
the old name-based API as a compatibility layer for existing config expansion,
MCP OAuth and WebFetch credential callers, but names are now labels looked up
against stable ``secret_id`` records rather than primary keys in a separate
sqlite3 schema.
"""

from __future__ import annotations

import logging
import re
import uuid
import builtins
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson

from kernel.core.paths import user_path
from kernel.core.secrets.manager import SecretRef
from kernel.core.secrets.types import OAuthToken, SecretDatabaseError, SecretNotFoundError
from kernel.core.storage import SecretStore
from kernel.core.storage.errors import StoreError
from kernel.core.storage.models import SecretAuditEvent, SecretRecord
from kernel.core.storage.sqlalchemy_async import make_engine, run_async

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = user_path("secrets.db")
_SECRET_RE = re.compile(r"\$\{secret:([^}]+)\}")
_SECRET_REF_PREFIX = "secret:"
_SCHEMA_VERSION = 2


class SecretManager:
    """Bootstrap secret service using UUID-backed SecretStore records."""

    def __init__(self, *, db_path: Path | None = None, home: Path | None = None) -> None:
        self._home = home if home is not None else (db_path or _DEFAULT_DB_PATH).parent
        self._db_path = self._home / SecretStore.db_name
        self._store: SecretStore | None = None
        self._conn = _SecretStoreCompatConnection(self)

    async def startup(self) -> None:
        """Open SecretStore and enforce local file permissions."""
        if self._store is not None:
            return
        try:
            legacy_rows = _read_legacy_rows(self._db_path)
            if legacy_rows is not None:
                _move_legacy_db_aside(self._db_path)
            self._store = SecretStore.open(self._home)
            self._enforce_permissions()
            if legacy_rows:
                self._import_legacy_rows(legacy_rows)
        except StoreError as exc:
            raise SecretDatabaseError(str(exc)) from exc
        logger.info("SecretManager started — db=%s", self._db_path)

    def close(self) -> None:
        """Close the SecretStore handle."""
        if self._store is not None:
            self._store.close()
            self._store = None

    def create(self, name: str, value: bytes, actor: str | None) -> SecretRef:
        """Create a new stable UUID secret."""
        self._ensure_unique_name(name)
        secret_id = str(uuid.uuid4())
        record = self._store_required().cas_secret(
            secret_id=secret_id,
            name=name,
            encrypted_payload=value,
            expected_revision=None,
            actor=actor,
        )
        return _ref_from_record(record)

    def list(self) -> list[SecretRecord]:
        """Return all UUID secret metadata."""
        return self._store_required().list_secrets()

    def resolve_id(self, secret_id_or_ref: str) -> bytes:
        """Resolve a UUID secret id or ``secret:<uuid>`` ref to bytes."""
        secret_id = _strip_secret_ref(secret_id_or_ref)
        value = self._store_required().get_ciphertext(secret_id)
        if value is None:
            raise KeyError(f"Secret not found: {secret_id}")
        self._store_required().append_audit(secret_id, "secret.resolve", None, {})
        return _decode_payload(value).value_bytes

    def rename(
        self,
        secret_id: str,
        name: str,
        *,
        expected_revision: int,
        actor: str | None,
    ) -> SecretRef:
        """Rename a UUID secret without changing its stable config ref."""
        self._ensure_unique_name(name, excluding_secret_id=secret_id)
        current_value = self._store_required().get_ciphertext(secret_id)
        if current_value is None:
            raise KeyError(f"Secret not found: {secret_id}")
        record = self._store_required().cas_secret(
            secret_id=secret_id,
            name=name,
            encrypted_payload=current_value,
            expected_revision=expected_revision,
            actor=actor,
        )
        self._store_required().append_audit(
            secret_id,
            "secret.rename",
            actor,
            {"name": name, "revision": record.revision},
        )
        return _ref_from_record(record)

    def delete_uuid(
        self,
        secret_id: str,
        *,
        expected_revision: int,
        actor: str | None,
        confirm: bool,
    ) -> bool:
        """Delete a UUID secret with explicit confirmation."""
        if not confirm:
            raise ValueError("Secret delete requires confirm=True")
        return self._store_required().delete_secret(
            secret_id,
            expected_revision=expected_revision,
            actor=actor,
        )

    def audit(self, secret_id: str | None = None) -> builtins.list[SecretAuditEvent]:
        """Return SecretStore audit events."""
        return self._store_required().audit_events(secret_id)

    @staticmethod
    def validate_config_ref(ref: str) -> str:
        """Validate stable config refs of the form ``secret:<uuid>``."""
        secret_id = _strip_secret_ref(ref)
        uuid.UUID(secret_id)
        return ref

    def get(self, name_or_ref: str) -> str | None:
        """Return plaintext for a legacy name or stable ``secret:<uuid>`` ref."""
        record = self._find_record(name_or_ref)
        if record is None:
            return None
        value = self._store_required().get_ciphertext(record.secret_id)
        if value is None:
            return None
        self._store_required().append_audit(record.secret_id, "secret.resolve", None, {})
        return _decode_payload(value).value

    def set(
        self,
        name: str,
        value: str,
        *,
        kind: str = "static",
        metadata: dict[str, Any] | None = None,
    ) -> SecretRef:
        """Create or update a name-labeled compatibility secret."""
        current = self._record_by_name(name)
        payload = _encode_payload(value=value, kind=kind, metadata=metadata or {})
        secret_id = current.secret_id if current is not None else str(uuid.uuid4())
        expected_revision = current.revision if current is not None else None
        record = self._store_required().cas_secret(
            secret_id=secret_id,
            name=name,
            encrypted_payload=payload,
            expected_revision=expected_revision,
            actor="system",
        )
        return _ref_from_record(record)

    def delete(self, name_or_ref: str) -> bool:
        """Delete a compatibility secret by name or stable ref."""
        record = self._find_record(name_or_ref)
        if record is None:
            return False
        return self._store_required().delete_secret(
            record.secret_id,
            expected_revision=record.revision,
            actor="system",
        )

    def list_names(self, *, kind: str | None = None) -> builtins.list[str]:
        """Return secret display names, optionally filtered by compatibility kind."""
        names: builtins.list[str] = []
        for record in self._store_required().list_secrets():
            value = self._store_required().get_ciphertext(record.secret_id)
            payload = _decode_payload(value or b"")
            if kind is None or payload.kind == kind:
                names.append(record.name)
        return sorted(names)

    def resolve(self, template: str) -> str:
        """Expand old ``${secret:name}`` templates or exact stable refs."""
        if template.startswith(_SECRET_REF_PREFIX):
            value = self.get(template)
            if value is None:
                raise SecretNotFoundError(f"Secret {template!r} referenced in config but not found")
            return value

        def _replace(m: re.Match[str]) -> str:
            secret_name = m.group(1)
            value = self.get(secret_name)
            if value is None:
                raise SecretNotFoundError(
                    f"Secret {secret_name!r} referenced in config but not found"
                )
            return value

        return _SECRET_RE.sub(_replace, template)

    def get_oauth_token(self, server_key: str) -> OAuthToken | None:
        """Return OAuth token metadata stored in one SecretStore row."""
        record = self._record_by_name(f"oauth:{server_key}")
        if record is None:
            return None
        raw = self._store_required().get_ciphertext(record.secret_id)
        payload = _decode_payload(raw or b"")
        if payload.kind != "oauth":
            return None
        oauth = payload.metadata.get("oauth", {})
        expires_at = oauth.get("expires_at")
        from datetime import datetime

        return OAuthToken(
            access_token=payload.value,
            refresh_token=oauth.get("refresh_token"),
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
            client_config=oauth.get("client_config", {}),
        )

    def set_oauth_token(self, server_key: str, token: OAuthToken) -> None:
        """Persist an OAuth token bundle in SecretStore."""
        self.set(
            f"oauth:{server_key}",
            token.access_token,
            kind="oauth",
            metadata={
                "oauth": {
                    "refresh_token": token.refresh_token,
                    "expires_at": token.expires_at.isoformat() if token.expires_at else None,
                    "client_config": token.client_config,
                    "server_key": server_key,
                }
            },
        )

    def delete_oauth_token(self, server_key: str) -> bool:
        """Delete OAuth token for a server."""
        return self.delete(f"oauth:{server_key}")

    def _ensure_unique_name(
        self,
        name: str,
        *,
        excluding_secret_id: str | None = None,
    ) -> None:
        record = self._record_by_name(name)
        if record is not None and record.secret_id != excluding_secret_id:
            raise ValueError(f"Secret name already exists: {name}")

    def _find_record(self, name_or_ref: str) -> SecretRecord | None:
        if name_or_ref.startswith(_SECRET_REF_PREFIX):
            return self._store_required().get_secret(_strip_secret_ref(name_or_ref))
        try:
            uuid.UUID(name_or_ref)
        except ValueError:
            return self._record_by_name(name_or_ref)
        return self._store_required().get_secret(name_or_ref)

    def _record_by_name(self, name: str) -> SecretRecord | None:
        for record in self._store_required().list_secrets():
            if record.name == name:
                return record
        return None

    def _store_required(self) -> SecretStore:
        if self._store is None:
            raise RuntimeError("SecretManager.startup() not called")
        return self._store

    def _enforce_permissions(self) -> None:
        try:
            current_mode = self._db_path.stat().st_mode & 0o777
            if current_mode != 0o600:
                self._db_path.chmod(0o600)
        except OSError:
            pass

    def _import_legacy_rows(self, rows: builtins.list["_LegacySecretRow"]) -> None:
        for row in rows:
            self.set(row.name, row.value, kind=row.kind, metadata=row.metadata)
        self._store_required().append_audit(
            None,
            "secret.legacy_import",
            "system",
            {"count": len(rows)},
        )


class _SecretStoreCompatConnection:
    """Tiny compatibility shim for old tests that read PRAGMA user_version."""

    def __init__(self, manager: SecretManager) -> None:
        self._manager = manager

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> "_SecretStoreCompatCursor":
        if sql.strip().lower() != "pragma user_version":
            raise SecretDatabaseError("SecretManager no longer exposes raw sqlite3 execution")
        return _SecretStoreCompatCursor((self._manager._store_required().schema_version,))


class _SecretStoreCompatCursor:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...]:
        return self._row


class _DecodedPayload:
    def __init__(self, *, value: str, kind: str, metadata: dict[str, Any]) -> None:
        self.value = value
        self.kind = kind
        self.metadata = metadata

    @property
    def value_bytes(self) -> bytes:
        return self.value.encode()


def _encode_payload(*, value: str, kind: str, metadata: dict[str, Any]) -> bytes:
    return orjson.dumps(
        {
            "format": "deepcli.secret.v1",
            "value": value,
            "kind": kind,
            "metadata": metadata,
        },
        option=orjson.OPT_SORT_KEYS,
    )


def _decode_payload(raw: bytes) -> _DecodedPayload:
    try:
        decoded = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return _DecodedPayload(value=raw.decode(), kind="static", metadata={})
    if not isinstance(decoded, dict) or decoded.get("format") != "deepcli.secret.v1":
        return _DecodedPayload(value=raw.decode(), kind="static", metadata={})
    return _DecodedPayload(
        value=str(decoded.get("value", "")),
        kind=str(decoded.get("kind", "static")),
        metadata=decoded.get("metadata", {}) if isinstance(decoded.get("metadata"), dict) else {},
    )


def _strip_secret_ref(ref: str) -> str:
    return ref.removeprefix(_SECRET_REF_PREFIX)


def _ref_from_record(record: SecretRecord) -> SecretRef:
    return SecretRef(
        secret_id=record.secret_id,
        ref=f"secret:{record.secret_id}",
        name=record.name,
        revision=record.revision,
    )


@dataclass(frozen=True, slots=True)
class _LegacySecretRow:
    name: str
    value: str
    kind: str
    metadata: dict[str, Any]


def _read_legacy_rows(db_path: Path) -> list[_LegacySecretRow] | None:
    if not db_path.exists():
        return None

    async def _read() -> list[_LegacySecretRow] | None:
        engine = make_engine(db_path)
        try:
            async with engine.connect() as conn:
                columns = (await conn.exec_driver_sql("PRAGMA table_info(secrets)")).fetchall()
                names = {str(row[1]) for row in columns}
                if "secret_id" in names:
                    return None
                if not {"name", "value"}.issubset(names):
                    return None
                rows = (
                    await conn.exec_driver_sql(
                        "SELECT name, value, type, metadata FROM secrets ORDER BY name"
                    )
                ).fetchall()
                oauth_columns = (
                    await conn.exec_driver_sql("PRAGMA table_info(oauth_tokens)")
                ).fetchall()
                has_oauth = bool(oauth_columns)
                oauth_by_name: dict[str, dict[str, Any]] = {}
                if has_oauth:
                    oauth_rows = (
                        await conn.exec_driver_sql(
                            """
                            SELECT name, refresh_token, expires_at, client_config, server_key
                            FROM oauth_tokens
                            """
                        )
                    ).fetchall()
                    for row in oauth_rows:
                        client_config = orjson.loads(row[3] or "{}")
                        oauth_by_name[str(row[0])] = {
                            "oauth": {
                                "refresh_token": row[1],
                                "expires_at": row[2],
                                "client_config": client_config,
                                "server_key": row[4],
                            }
                        }
                legacy: list[_LegacySecretRow] = []
                for row in rows:
                    metadata = orjson.loads(row[3] or "{}")
                    if not isinstance(metadata, dict):
                        metadata = {}
                    metadata.update(oauth_by_name.get(str(row[0]), {}))
                    legacy.append(
                        _LegacySecretRow(
                            name=str(row[0]),
                            value=str(row[1]),
                            kind=str(row[2] or "static"),
                            metadata=metadata,
                        )
                    )
                return legacy
        finally:
            await engine.dispose()

    return run_async(_read)


def _move_legacy_db_aside(db_path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.legacy-{stamp}")
    db_path.replace(backup)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


__all__ = ["OAuthToken", "SecretDatabaseError", "SecretManager", "SecretNotFoundError", "SecretRef"]
