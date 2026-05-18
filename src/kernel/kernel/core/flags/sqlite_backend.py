"""ResourceStore-backed startup flag backend."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kernel.core.storage import tables
from kernel.core.storage import ResourceStore, RevisionConflict


@dataclass(frozen=True, slots=True)
class FlagSectionRecord:
    """SQLite flag section row."""

    section: str
    payload: dict[str, Any]
    revision: int
    updated_at: str
    updated_by_agent_id: str | None
    payload_hash: str


class FlagSQLiteBackend:
    """Flag sections persisted in ResourceStore ``global.db``."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store

    def read(self, section: str) -> FlagSectionRecord | None:
        row = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.flag_sections.c.section,
                    tables.flag_sections.c.payload_json,
                    tables.flag_sections.c.revision,
                    tables.flag_sections.c.updated_at,
                    tables.flag_sections.c.updated_by_agent_id,
                    tables.flag_sections.c.payload_hash,
                ).where(tables.flag_sections.c.section == section)
            ).fetchone()
        )
        return _record_from_row(row) if row is not None else None

    def read_all_raw(self) -> dict[str, Any]:
        """Return all flag sections as raw dictionaries."""
        rows = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.flag_sections.c.section,
                    tables.flag_sections.c.payload_json,
                ).order_by(tables.flag_sections.c.section)
            ).fetchall()
        )
        return {str(row["section"]): orjson.loads(row["payload_json"]) for row in rows}

    def write(
        self,
        *,
        section: str,
        payload: dict[str, Any],
        expected_revision: int | None,
        actor: str | None,
    ) -> FlagSectionRecord:
        """Write one startup flag section with optimistic revision checks."""
        payload_json = _dump_payload(payload)
        payload_hash = _hash(payload_json)

        def _write(conn: Any) -> FlagSectionRecord:
            current = conn.execute(
                sa.select(tables.flag_sections.c.revision, tables.flag_sections.c.payload_hash).where(
                    tables.flag_sections.c.section == section
                )
            ).fetchone()
            previous_hash: str | None = None
            if current is None:
                if expected_revision not in (None, 0):
                    raise RevisionConflict(
                        f"Flag section {section} does not exist",
                        resource_key=f"flags.{section}",
                        current_revision=None,
                        current_hash=None,
                    )
                revision = 1
            else:
                current_revision = int(current["revision"])
                previous_hash = str(current["payload_hash"])
                if expected_revision != current_revision:
                    raise RevisionConflict(
                        f"Flag section {section} revision conflict",
                        resource_key=f"flags.{section}",
                        current_revision=current_revision,
                        current_hash=previous_hash,
                    )
                revision = current_revision + 1

            now = _now_iso()
            conn.execute(
                sqlite_insert(tables.flag_sections)
                .values(
                    section=section,
                    payload_json=payload_json,
                    revision=revision,
                    updated_at=now,
                    updated_by_agent_id=actor,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    index_elements=[tables.flag_sections.c.section],
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
                tables.flag_events.insert().values(
                    section=section,
                    revision=revision,
                    event_type="flag.write",
                    updated_at=now,
                    updated_by_agent_id=actor,
                    payload_hash=payload_hash,
                    previous_payload_hash=previous_hash,
                )
            )
            conn.execute(
                sqlite_insert(tables.resource_revisions)
                .values(
                    resource_key=f"flags.{section}",
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
            row = conn.execute(
                sa.select(
                    tables.flag_sections.c.section,
                    tables.flag_sections.c.payload_json,
                    tables.flag_sections.c.revision,
                    tables.flag_sections.c.updated_at,
                    tables.flag_sections.c.updated_by_agent_id,
                    tables.flag_sections.c.payload_hash,
                ).where(tables.flag_sections.c.section == section)
            ).fetchone()
            return _record_from_row(row)

        return self._store.write_tx(_write)


def _record_from_row(row: Any) -> FlagSectionRecord:
    return FlagSectionRecord(
        section=str(row["section"]),
        payload=orjson.loads(row["payload_json"]),
        revision=int(row["revision"]),
        updated_at=str(row["updated_at"]),
        updated_by_agent_id=row["updated_by_agent_id"],
        payload_hash=str(row["payload_hash"]),
    )


def _dump_payload(payload: dict[str, Any]) -> str:
    return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS).decode()


def _hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
