"""SQLAlchemy-backed shared SecretStore library."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kernel.core.storage import tables
from kernel.core.storage.errors import StoreBusyTimeout, StoreMigrationError, StoreOpenError
from kernel.core.storage.migrations.secrets import MIGRATIONS, SCHEMA_VERSION
from kernel.core.storage.models import SecretAuditEvent, SecretRecord
from kernel.core.storage.sqlalchemy_async import (
    apply_store_migrations,
    make_engine,
    map_operational_error,
    run_async,
    run_sync_tx,
)

T = TypeVar("T")


class SecretStore:
    """Shared-library access to ``secrets.db``."""

    db_name = "secrets.db"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._closed = False

    @classmethod
    def open(cls, home: Path, *, apply_migrations: bool = True) -> "SecretStore":
        """Open ``home/secrets.db`` and optionally apply migrations."""
        db_path = home / cls.db_name
        db_path.parent.mkdir(parents=True, exist_ok=True)

        async def _open() -> None:
            engine = make_engine(db_path)
            try:
                async with engine.connect() as conn:
                    if apply_migrations:
                        await apply_store_migrations(
                            conn,
                            store_name="SecretStore",
                            schema_version=SCHEMA_VERSION,
                            migrations=MIGRATIONS,
                        )
            finally:
                await engine.dispose()

        try:
            run_async(_open)
        except StoreMigrationError:
            raise
        except StoreOpenError:
            raise
        except Exception as exc:
            raise StoreOpenError(f"Cannot open SecretStore at {db_path}: {exc}") from exc
        return cls(db_path)

    def close(self) -> None:
        """Close the SQLite connection. Idempotent."""
        self._closed = True

    def get_secret(self, secret_id: str) -> SecretRecord | None:
        """Return secret metadata by id."""
        row = self.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.secrets.c.secret_id,
                    tables.secrets.c.name,
                    tables.secrets.c.revision,
                    tables.secrets.c.created_at,
                    tables.secrets.c.updated_at,
                    tables.secrets.c.created_by_agent_id,
                    tables.secrets.c.updated_by_agent_id,
                    tables.secrets.c.payload_hash,
                ).where(tables.secrets.c.secret_id == secret_id)
            ).fetchone()
        )
        return _secret_from_row(row) if row is not None else None

    def get_ciphertext(self, secret_id: str) -> bytes | None:
        """Return encrypted payload bytes for privileged resolution."""
        row = self.read_tx(
            lambda conn: conn.execute(
                sa.select(tables.secrets.c.value_ciphertext).where(
                    tables.secrets.c.secret_id == secret_id
                )
            ).fetchone()
        )
        return bytes(row["value_ciphertext"]) if row is not None else None

    def list_secrets(self) -> list[SecretRecord]:
        """Return all secret metadata ordered by name."""
        rows = self.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.secrets.c.secret_id,
                    tables.secrets.c.name,
                    tables.secrets.c.revision,
                    tables.secrets.c.created_at,
                    tables.secrets.c.updated_at,
                    tables.secrets.c.created_by_agent_id,
                    tables.secrets.c.updated_by_agent_id,
                    tables.secrets.c.payload_hash,
                ).order_by(tables.secrets.c.name)
            ).fetchall()
        )
        return [_secret_from_row(row) for row in rows]

    def cas_secret(
        self,
        *,
        secret_id: str,
        name: str,
        encrypted_payload: bytes,
        expected_revision: int | None,
        actor: str | None,
    ) -> SecretRecord:
        """Create or update one secret with optimistic revision checks."""
        from kernel.core.storage.errors import RevisionConflict

        payload_hash = hashlib.sha256(encrypted_payload).hexdigest()

        def _write(conn: Any) -> SecretRecord:
            current = conn.execute(
                sa.select(tables.secrets.c.revision, tables.secrets.c.payload_hash).where(
                    tables.secrets.c.secret_id == secret_id
                )
            ).fetchone()
            now = _now_iso()
            previous_hash: str | None = None
            if current is None:
                if expected_revision not in (None, 0):
                    raise RevisionConflict(
                        f"Secret {secret_id} does not exist",
                        resource_key=f"secret.{secret_id}",
                        current_revision=None,
                        current_hash=None,
                    )
                revision = 1
                created_at = now
                created_by = actor
            else:
                current_revision = int(current["revision"])
                previous_hash = str(current["payload_hash"])
                if expected_revision != current_revision:
                    raise RevisionConflict(
                        f"Secret {secret_id} revision conflict",
                        resource_key=f"secret.{secret_id}",
                        current_revision=current_revision,
                        current_hash=previous_hash,
                    )
                revision = current_revision + 1
                existing = conn.execute(
                    sa.select(tables.secrets.c.created_at, tables.secrets.c.created_by_agent_id).where(
                        tables.secrets.c.secret_id == secret_id
                    )
                ).fetchone()
                created_at = str(existing["created_at"])
                created_by = existing["created_by_agent_id"]

            conn.execute(
                sqlite_insert(tables.secrets)
                .values(
                    secret_id=secret_id,
                    name=name,
                    value_ciphertext=encrypted_payload,
                    revision=revision,
                    created_at=created_at,
                    updated_at=now,
                    created_by_agent_id=created_by,
                    updated_by_agent_id=actor,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    index_elements=[tables.secrets.c.secret_id],
                    set_={
                        "name": name,
                        "value_ciphertext": encrypted_payload,
                        "revision": revision,
                        "updated_at": now,
                        "updated_by_agent_id": actor,
                        "payload_hash": payload_hash,
                    },
                ),
            )
            self._append_audit_in_tx(
                conn,
                secret_id=secret_id,
                event_type="secret.write",
                actor=actor,
                metadata={"name": name, "revision": revision},
                payload_hash=payload_hash,
                previous_hash=previous_hash,
                created_at=now,
            )
            row = conn.execute(
                sa.select(
                    tables.secrets.c.secret_id,
                    tables.secrets.c.name,
                    tables.secrets.c.revision,
                    tables.secrets.c.created_at,
                    tables.secrets.c.updated_at,
                    tables.secrets.c.created_by_agent_id,
                    tables.secrets.c.updated_by_agent_id,
                    tables.secrets.c.payload_hash,
                ).where(tables.secrets.c.secret_id == secret_id)
            ).fetchone()
            return _secret_from_row(row)

        return self.write_tx(_write)

    def append_audit(
        self,
        secret_id: str | None,
        event_type: str,
        actor: str | None,
        metadata: dict[str, object] | None = None,
    ) -> SecretAuditEvent:
        """Append one secret audit event."""
        return self.write_tx(
            lambda conn: self._append_audit_in_tx(
                conn,
                secret_id=secret_id,
                event_type=event_type,
                actor=actor,
                metadata=metadata or {},
                payload_hash=None,
                previous_hash=None,
                created_at=_now_iso(),
            )
        )

    def audit_events(self, secret_id: str | None = None) -> list[SecretAuditEvent]:
        """Return audit events, optionally for one secret."""
        if secret_id is None:
            rows = self.read_tx(
                lambda conn: conn.execute(
                    sa.select(
                        tables.secret_events.c.id,
                        tables.secret_events.c.secret_id,
                        tables.secret_events.c.event_type,
                        tables.secret_events.c.actor_agent_id,
                        tables.secret_events.c.created_at,
                        tables.secret_events.c.metadata_json,
                        tables.secret_events.c.payload_hash,
                        tables.secret_events.c.previous_payload_hash,
                    ).order_by(tables.secret_events.c.id)
                ).fetchall()
            )
        else:
            rows = self.read_tx(
                lambda conn: conn.execute(
                    sa.select(
                        tables.secret_events.c.id,
                        tables.secret_events.c.secret_id,
                        tables.secret_events.c.event_type,
                        tables.secret_events.c.actor_agent_id,
                        tables.secret_events.c.created_at,
                        tables.secret_events.c.metadata_json,
                        tables.secret_events.c.payload_hash,
                        tables.secret_events.c.previous_payload_hash,
                    )
                    .where(tables.secret_events.c.secret_id == secret_id)
                    .order_by(tables.secret_events.c.id)
                ).fetchall()
            )
        return [_audit_from_row(row) for row in rows]

    def delete_secret(
        self,
        secret_id: str,
        *,
        expected_revision: int,
        actor: str | None,
    ) -> bool:
        """Delete one secret if the revision matches."""
        from kernel.core.storage.errors import RevisionConflict

        def _write(conn: Any) -> bool:
            current = conn.execute(
                sa.select(tables.secrets.c.revision, tables.secrets.c.payload_hash).where(
                    tables.secrets.c.secret_id == secret_id
                )
            ).fetchone()
            if current is None:
                return False
            current_revision = int(current["revision"])
            current_hash = str(current["payload_hash"])
            if expected_revision != current_revision:
                raise RevisionConflict(
                    f"Secret {secret_id} revision conflict",
                    resource_key=f"secret.{secret_id}",
                    current_revision=current_revision,
                    current_hash=current_hash,
                )
            conn.execute(tables.secrets.delete().where(tables.secrets.c.secret_id == secret_id))
            self._append_audit_in_tx(
                conn,
                secret_id=secret_id,
                event_type="secret.delete",
                actor=actor,
                metadata={"revision": current_revision},
                payload_hash=None,
                previous_hash=current_hash,
                created_at=_now_iso(),
            )
            return True

        return self.write_tx(_write)

    def _append_audit_in_tx(
        self,
        conn: Any,
        *,
        secret_id: str | None,
        event_type: str,
        actor: str | None,
        metadata: dict[str, object],
        payload_hash: str | None,
        previous_hash: str | None,
        created_at: str,
    ) -> SecretAuditEvent:
        metadata_json = orjson.dumps(metadata, option=orjson.OPT_SORT_KEYS).decode()
        cur = conn.execute(
            tables.secret_events.insert().values(
                secret_id=secret_id,
                event_type=event_type,
                actor_agent_id=actor,
                created_at=created_at,
                metadata_json=metadata_json,
                payload_hash=payload_hash,
                previous_payload_hash=previous_hash,
            )
        )
        if cur.lastrowid is None:
            raise StoreOpenError("SecretStore audit insert did not return an id")
        row = conn.execute(
            sa.select(
                tables.secret_events.c.id,
                tables.secret_events.c.secret_id,
                tables.secret_events.c.event_type,
                tables.secret_events.c.actor_agent_id,
                tables.secret_events.c.created_at,
                tables.secret_events.c.metadata_json,
                tables.secret_events.c.payload_hash,
                tables.secret_events.c.previous_payload_hash,
            ).where(tables.secret_events.c.id == int(cur.lastrowid))
        ).fetchone()
        return _audit_from_row(row)

    def read_tx(self, fn: Callable[[Any], T]) -> T:
        """Run a read transaction and return ``fn``'s result."""
        self._require_open()
        return run_async(lambda: run_sync_tx(self.db_path, fn, immediate=False))

    def write_tx(self, fn: Callable[[Any], T]) -> T:
        """Run a short ``BEGIN IMMEDIATE`` write transaction."""
        self._require_open()
        return run_async(lambda: run_sync_tx(self.db_path, fn, immediate=True))

    @property
    def schema_version(self) -> int:
        """Current ``PRAGMA user_version``."""
        self._require_open()
        return run_async(lambda: _read_pragma_int(self.db_path, "user_version"))

    @property
    def journal_mode(self) -> str:
        """Current SQLite journal mode."""
        self._require_open()
        return run_async(lambda: _read_pragma_text(self.db_path, "journal_mode"))

    def _require_open(self) -> None:
        if self._closed:
            raise StoreOpenError("SecretStore is closed")


async def _read_pragma_int(db_path: Path, name: str) -> int:
    engine = make_engine(db_path)
    try:
        async with engine.connect() as conn:
            row = (await conn.exec_driver_sql(f"PRAGMA {name}")).fetchone()
            return int(row[0]) if row is not None else 0
    except Exception as exc:
        mapped = map_operational_error(exc)
        if isinstance(mapped, StoreBusyTimeout):
            raise mapped from exc
        raise StoreOpenError(str(mapped)) from exc
    finally:
        await engine.dispose()


async def _read_pragma_text(db_path: Path, name: str) -> str:
    engine = make_engine(db_path)
    try:
        async with engine.connect() as conn:
            row = (await conn.exec_driver_sql(f"PRAGMA {name}")).fetchone()
            return str(row[0]) if row is not None else ""
    except Exception as exc:
        mapped = map_operational_error(exc)
        if isinstance(mapped, StoreBusyTimeout):
            raise mapped from exc
        raise StoreOpenError(str(mapped)) from exc
    finally:
        await engine.dispose()


def _secret_from_row(row: Any) -> SecretRecord:
    return SecretRecord(
        secret_id=str(row["secret_id"]),
        name=str(row["name"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        created_by_agent_id=row["created_by_agent_id"],
        updated_by_agent_id=row["updated_by_agent_id"],
        payload_hash=str(row["payload_hash"]),
    )


def _audit_from_row(row: Any) -> SecretAuditEvent:
    return SecretAuditEvent(
        id=int(row["id"]),
        secret_id=row["secret_id"],
        event_type=str(row["event_type"]),
        actor_agent_id=row["actor_agent_id"],
        created_at=str(row["created_at"]),
        metadata_json=str(row["metadata_json"]),
        payload_hash=row["payload_hash"],
        previous_payload_hash=row["previous_payload_hash"],
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
