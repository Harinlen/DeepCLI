"""SQLAlchemy-backed shared ResourceStore library."""

from __future__ import annotations

import hashlib
import orjson
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kernel.core.storage import tables
from kernel.core.storage.errors import (
    BackupError,
    RevisionConflict,
    StoreBusyTimeout,
    StoreMigrationError,
    StoreOpenError,
)
from kernel.core.storage.migrations.resource import MIGRATIONS, SCHEMA_VERSION
from kernel.core.storage.models import BackupRecord, ExportReport, ResourceEvent, ResourceRecord
from kernel.core.storage.sqlalchemy_async import (
    apply_store_migrations,
    make_engine,
    map_operational_error,
    run_async,
    run_sync_tx,
)

T = TypeVar("T")


class ResourceStore:
    """Shared-library access to ``global.db``.

    The store owns connection setup, WAL PRAGMAs, baseline migrations and
    transaction boundaries. Business managers own domain validation and table
    semantics above these primitives.
    """

    db_name = "global.db"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._closed = False

    @classmethod
    def open(cls, home: Path, *, apply_migrations: bool = True) -> "ResourceStore":
        """Open ``home/global.db`` and optionally apply migrations."""
        db_path = home / cls.db_name
        db_path.parent.mkdir(parents=True, exist_ok=True)

        async def _open() -> None:
            engine = make_engine(db_path)
            try:
                async with engine.connect() as conn:
                    if apply_migrations:
                        await apply_store_migrations(
                            conn,
                            store_name="ResourceStore",
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
            raise StoreOpenError(f"Cannot open ResourceStore at {db_path}: {exc}") from exc
        return cls(db_path)

    def close(self) -> None:
        """Close the SQLite connection. Idempotent."""
        self._closed = True

    def get_resource(self, resource_key: str) -> ResourceRecord | None:
        """Return one generic resource row by key."""
        row = self.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.global_resources.c.resource_key,
                    tables.global_resources.c.payload_json,
                    tables.global_resources.c.revision,
                    tables.global_resources.c.updated_at,
                    tables.global_resources.c.updated_by_agent_id,
                    tables.global_resources.c.payload_hash,
                ).where(tables.global_resources.c.resource_key == resource_key)
            ).fetchone()
        )
        return _resource_from_row(row) if row is not None else None

    def cas_put_resource(
        self,
        resource_key: str,
        payload_json: str,
        *,
        expected_revision: int | None = None,
        actor: str | None = None,
    ) -> ResourceRecord:
        """Create or update a generic resource with optimistic revision checks."""

        def _write(conn: Any) -> ResourceRecord:
            current = conn.execute(
                sa.select(
                    tables.global_resources.c.revision,
                    tables.global_resources.c.payload_hash,
                ).where(tables.global_resources.c.resource_key == resource_key)
            ).fetchone()
            if current is None:
                if expected_revision not in (None, 0):
                    raise RevisionConflict(
                        f"Resource {resource_key!r} does not exist",
                        resource_key=resource_key,
                        current_revision=None,
                        current_hash=None,
                    )
                revision = 1
                previous_hash = None
            else:
                current_revision = int(current["revision"])
                current_hash = str(current["payload_hash"])
                if expected_revision is None:
                    raise RevisionConflict(
                        f"Resource {resource_key!r} already exists",
                        resource_key=resource_key,
                        current_revision=current_revision,
                        current_hash=current_hash,
                    )
                if expected_revision != current_revision:
                    raise RevisionConflict(
                        f"Resource {resource_key!r} revision conflict",
                        resource_key=resource_key,
                        current_revision=current_revision,
                        current_hash=current_hash,
                    )
                revision = current_revision + 1
                previous_hash = current_hash

            now = _now_iso()
            payload_hash = _hash_payload(payload_json)
            conn.execute(
                sqlite_insert(tables.global_resources)
                .values(
                    resource_key=resource_key,
                    payload_json=payload_json,
                    revision=revision,
                    updated_at=now,
                    updated_by_agent_id=actor,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    index_elements=[tables.global_resources.c.resource_key],
                    set_={
                        "payload_json": payload_json,
                        "revision": revision,
                        "updated_at": now,
                        "updated_by_agent_id": actor,
                        "payload_hash": payload_hash,
                    },
                )
            )
            conn.execute(
                sqlite_insert(tables.resource_revisions)
                .values(
                    resource_key=resource_key,
                    revision=revision,
                    updated_at=now,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    index_elements=[tables.resource_revisions.c.resource_key],
                    set_={
                        "revision": revision,
                        "updated_at": now,
                        "payload_hash": payload_hash,
                    },
                )
            )
            self._append_event_in_tx(
                conn,
                resource_key=resource_key,
                revision=revision,
                event_type="resource.put",
                actor=actor,
                payload_hash=payload_hash,
                previous_hash=previous_hash,
                updated_at=now,
            )
            row = conn.execute(
                sa.select(
                    tables.global_resources.c.resource_key,
                    tables.global_resources.c.payload_json,
                    tables.global_resources.c.revision,
                    tables.global_resources.c.updated_at,
                    tables.global_resources.c.updated_by_agent_id,
                    tables.global_resources.c.payload_hash,
                ).where(tables.global_resources.c.resource_key == resource_key)
            ).fetchone()
            return _resource_from_row(row)

        return self.write_tx(_write)

    def current_revisions(self, prefix: str | None = None) -> dict[str, int]:
        """Return current resource revisions, optionally filtered by prefix."""
        if prefix is None:
            rows = self.read_tx(
                lambda conn: conn.execute(
                    sa.select(
                        tables.resource_revisions.c.resource_key,
                        tables.resource_revisions.c.revision,
                    ).order_by(tables.resource_revisions.c.resource_key)
                ).fetchall()
            )
        else:
            rows = self.read_tx(
                lambda conn: conn.execute(
                    sa.select(
                        tables.resource_revisions.c.resource_key,
                        tables.resource_revisions.c.revision,
                    )
                    .where(tables.resource_revisions.c.resource_key.like(f"{prefix}%"))
                    .order_by(tables.resource_revisions.c.resource_key)
                ).fetchall()
            )
        return {str(row["resource_key"]): int(row["revision"]) for row in rows}

    def append_event(
        self,
        resource_key: str,
        revision: int,
        event_type: str,
        actor: str | None,
        payload_hash: str,
        previous_hash: str | None = None,
    ) -> ResourceEvent:
        """Append a generic resource event in its own transaction."""
        return self.write_tx(
            lambda conn: self._append_event_in_tx(
                conn,
                resource_key=resource_key,
                revision=revision,
                event_type=event_type,
                actor=actor,
                payload_hash=payload_hash,
                previous_hash=previous_hash,
                updated_at=_now_iso(),
            )
        )

    def backup(self, output_path: Path) -> BackupRecord:
        """Snapshot the live SQLite database to ``output_path``."""
        self._require_open()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async def _backup() -> None:
            engine = make_engine(self.db_path)
            try:
                async with engine.connect() as conn:
                    await conn.exec_driver_sql("VACUUM INTO ?", (str(output_path),))
            finally:
                await engine.dispose()

        try:
            run_async(_backup)
        except Exception as exc:
            raise BackupError(f"Cannot backup ResourceStore to {output_path}: {exc}") from exc
        checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return BackupRecord(
            path=str(output_path),
            checksum=checksum,
            created_at=_now_iso(),
            source_schema_version=self.schema_version,
        )

    def export(
        self,
        format: str,
        output_path: Path | None = None,
        *,
        include_history: bool = False,
        dry_run: bool = True,
    ) -> ExportReport:
        """Report or write a JSON export of generic resources."""
        if format != "json":
            return ExportReport(
                dry_run=dry_run,
                format=format,
                output_path=str(output_path) if output_path is not None else None,
                resource_count=0,
                event_count=0,
                warnings=(f"unsupported export format: {format}",),
            )

        rows, events = self.read_tx(
            lambda conn: (
                conn.execute(
                    sa.select(
                        tables.global_resources.c.resource_key,
                        tables.global_resources.c.payload_json,
                        tables.global_resources.c.revision,
                        tables.global_resources.c.updated_at,
                        tables.global_resources.c.updated_by_agent_id,
                        tables.global_resources.c.payload_hash,
                    ).order_by(tables.global_resources.c.resource_key)
                ).fetchall(),
                conn.execute(sa.select(sa.func.count()).select_from(tables.global_resource_events))
                .fetchone()[0],
            )
        )
        if output_path is not None and not dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": self.schema_version,
                "resources": [_row_dict(row) for row in rows],
            }
            if include_history:
                payload["events"] = self.read_tx(
                    lambda conn: [
                        _row_dict(row)
                        for row in conn.execute(
                            sa.select(
                                tables.global_resource_events.c.id,
                                tables.global_resource_events.c.resource_key,
                                tables.global_resource_events.c.revision,
                                tables.global_resource_events.c.event_type,
                                tables.global_resource_events.c.updated_at,
                                tables.global_resource_events.c.updated_by_agent_id,
                                tables.global_resource_events.c.payload_hash,
                                tables.global_resource_events.c.previous_payload_hash,
                            ).order_by(tables.global_resource_events.c.id)
                        ).fetchall()
                    ]
                )
            output_path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
        return ExportReport(
            dry_run=dry_run,
            format=format,
            output_path=str(output_path) if output_path is not None else None,
            resource_count=len(rows),
            event_count=int(events),
            warnings=(),
        )

    def _append_event_in_tx(
        self,
        conn: Any,
        *,
        resource_key: str,
        revision: int,
        event_type: str,
        actor: str | None,
        payload_hash: str,
        previous_hash: str | None,
        updated_at: str,
    ) -> ResourceEvent:
        cur = conn.execute(
            tables.global_resource_events.insert().values(
                resource_key=resource_key,
                revision=revision,
                event_type=event_type,
                updated_at=updated_at,
                updated_by_agent_id=actor,
                payload_hash=payload_hash,
                previous_payload_hash=previous_hash,
            )
        )
        if cur.lastrowid is None:
            raise StoreOpenError("ResourceStore event insert did not return an id")
        event_id = int(cur.lastrowid)
        row = conn.execute(
            sa.select(
                tables.global_resource_events.c.id,
                tables.global_resource_events.c.resource_key,
                tables.global_resource_events.c.revision,
                tables.global_resource_events.c.event_type,
                tables.global_resource_events.c.updated_at,
                tables.global_resource_events.c.updated_by_agent_id,
                tables.global_resource_events.c.payload_hash,
                tables.global_resource_events.c.previous_payload_hash,
            ).where(tables.global_resource_events.c.id == event_id)
        ).fetchone()
        return _event_from_row(row)

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
            raise StoreOpenError("ResourceStore is closed")


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


def _resource_from_row(row: Any) -> ResourceRecord:
    return ResourceRecord(
        resource_key=str(row["resource_key"]),
        payload_json=str(row["payload_json"]),
        revision=int(row["revision"]),
        updated_at=str(row["updated_at"]),
        updated_by_agent_id=row["updated_by_agent_id"],
        payload_hash=str(row["payload_hash"]),
    )


def _event_from_row(row: Any) -> ResourceEvent:
    return ResourceEvent(
        id=int(row["id"]),
        resource_key=str(row["resource_key"]),
        revision=int(row["revision"]),
        event_type=str(row["event_type"]),
        updated_at=str(row["updated_at"]),
        updated_by_agent_id=row["updated_by_agent_id"],
        payload_hash=str(row["payload_hash"]),
        previous_payload_hash=row["previous_payload_hash"],
    )


def _hash_payload(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()


def _row_dict(row: Any) -> dict[str, Any]:
    return {str(key): value for key, value in row.items()}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
