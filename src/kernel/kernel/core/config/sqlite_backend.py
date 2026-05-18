"""ResourceStore-backed global config section backend."""

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

GLOBAL_SCOPE = "global"
GLOBAL_SCOPE_ID = "_"


@dataclass(frozen=True, slots=True)
class ConfigSectionRecord:
    """SQLite config section row."""

    scope: str
    scope_id: str
    file: str
    section: str
    payload: dict[str, Any]
    revision: int
    updated_at: str
    updated_by_agent_id: str | None
    payload_hash: str


class ConfigSQLiteBackend:
    """Config sections persisted in ResourceStore ``global.db``."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store

    def read(
        self,
        *,
        file: str,
        section: str,
        scope: str = GLOBAL_SCOPE,
        scope_id: str = GLOBAL_SCOPE_ID,
    ) -> ConfigSectionRecord | None:
        row = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.config_sections.c.scope,
                    tables.config_sections.c.scope_id,
                    tables.config_sections.c.file,
                    tables.config_sections.c.section,
                    tables.config_sections.c.payload_json,
                    tables.config_sections.c.revision,
                    tables.config_sections.c.updated_at,
                    tables.config_sections.c.updated_by_agent_id,
                    tables.config_sections.c.payload_hash,
                ).where(
                    tables.config_sections.c.scope == scope,
                    tables.config_sections.c.scope_id == scope_id,
                    tables.config_sections.c.file == file,
                    tables.config_sections.c.section == section,
                )
            ).fetchone()
        )
        return _record_from_row(row) if row is not None else None

    def read_global_raw(self) -> dict[str, dict[str, Any]]:
        """Return global config as ``{file: {section: payload}}``."""
        rows = self._store.read_tx(
            lambda conn: conn.execute(
                sa.select(
                    tables.config_sections.c.file,
                    tables.config_sections.c.section,
                    tables.config_sections.c.payload_json,
                )
                .where(
                    tables.config_sections.c.scope == GLOBAL_SCOPE,
                    tables.config_sections.c.scope_id == GLOBAL_SCOPE_ID,
                )
                .order_by(tables.config_sections.c.file, tables.config_sections.c.section)
            ).fetchall()
        )
        raw: dict[str, dict[str, Any]] = {}
        for row in rows:
            raw.setdefault(str(row["file"]), {})[str(row["section"])] = orjson.loads(
                row["payload_json"]
            )
        return raw

    def write(
        self,
        *,
        file: str,
        section: str,
        payload: dict[str, Any],
        expected_revision: int | None,
        actor: str | None,
        scope: str = GLOBAL_SCOPE,
        scope_id: str = GLOBAL_SCOPE_ID,
    ) -> ConfigSectionRecord:
        """Write one config section with optimistic revision checks."""
        payload_json = _dump_payload(payload)
        payload_hash = _hash(payload_json)

        def _write(conn: Any) -> ConfigSectionRecord:
            current = conn.execute(
                sa.select(
                    tables.config_sections.c.revision,
                    tables.config_sections.c.payload_hash,
                ).where(
                    tables.config_sections.c.scope == scope,
                    tables.config_sections.c.scope_id == scope_id,
                    tables.config_sections.c.file == file,
                    tables.config_sections.c.section == section,
                )
            ).fetchone()
            previous_hash: str | None = None
            if current is None:
                if expected_revision not in (None, 0):
                    raise RevisionConflict(
                        f"Config section {file}.{section} does not exist",
                        resource_key=_resource_key(scope, scope_id, file, section),
                        current_revision=None,
                        current_hash=None,
                    )
                revision = 1
            else:
                current_revision = int(current["revision"])
                previous_hash = str(current["payload_hash"])
                if expected_revision != current_revision:
                    raise RevisionConflict(
                        f"Config section {file}.{section} revision conflict",
                        resource_key=_resource_key(scope, scope_id, file, section),
                        current_revision=current_revision,
                        current_hash=previous_hash,
                    )
                revision = current_revision + 1

            now = _now_iso()
            conn.execute(
                sqlite_insert(tables.config_sections)
                .values(
                    scope=scope,
                    scope_id=scope_id,
                    file=file,
                    section=section,
                    payload_json=payload_json,
                    revision=revision,
                    updated_at=now,
                    updated_by_agent_id=actor,
                    payload_hash=payload_hash,
                )
                .on_conflict_do_update(
                    index_elements=[
                        tables.config_sections.c.scope,
                        tables.config_sections.c.scope_id,
                        tables.config_sections.c.file,
                        tables.config_sections.c.section,
                    ],
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
                tables.config_events.insert().values(
                    scope=scope,
                    scope_id=scope_id,
                    file=file,
                    section=section,
                    revision=revision,
                    event_type="config.write",
                    updated_at=now,
                    updated_by_agent_id=actor,
                    payload_hash=payload_hash,
                    previous_payload_hash=previous_hash,
                )
            )
            conn.execute(
                sqlite_insert(tables.resource_revisions)
                .values(
                    resource_key=_resource_key(scope, scope_id, file, section),
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
                    tables.config_sections.c.scope,
                    tables.config_sections.c.scope_id,
                    tables.config_sections.c.file,
                    tables.config_sections.c.section,
                    tables.config_sections.c.payload_json,
                    tables.config_sections.c.revision,
                    tables.config_sections.c.updated_at,
                    tables.config_sections.c.updated_by_agent_id,
                    tables.config_sections.c.payload_hash,
                ).where(
                    tables.config_sections.c.scope == scope,
                    tables.config_sections.c.scope_id == scope_id,
                    tables.config_sections.c.file == file,
                    tables.config_sections.c.section == section,
                )
            ).fetchone()
            return _record_from_row(row)

        return self._store.write_tx(_write)


def _record_from_row(row: Any) -> ConfigSectionRecord:
    return ConfigSectionRecord(
        scope=str(row["scope"]),
        scope_id=str(row["scope_id"]),
        file=str(row["file"]),
        section=str(row["section"]),
        payload=orjson.loads(row["payload_json"]),
        revision=int(row["revision"]),
        updated_at=str(row["updated_at"]),
        updated_by_agent_id=row["updated_by_agent_id"],
        payload_hash=str(row["payload_hash"]),
    )


def _resource_key(scope: str, scope_id: str, file: str, section: str) -> str:
    return f"config.{scope}.{scope_id}.{file}.{section}"


def _dump_payload(payload: dict[str, Any]) -> str:
    return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS).decode()


def _hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
