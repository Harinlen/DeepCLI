"""ResourceStore schema version 8 spawned run metadata columns."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from kernel.core.storage import tables


_COLUMNS = (
    tables.agent_spawned_runs.c.result_json,
    tables.agent_spawned_runs.c.provenance_json,
    tables.agent_spawned_runs.c.binding_id,
    tables.agent_spawned_runs.c.timeout_seconds,
    tables.agent_spawned_runs.c.wait_mode,
    tables.agent_spawned_runs.c.reply_back_enabled,
    tables.agent_spawned_runs.c.announce_enabled,
    tables.agent_spawned_runs.c.acp_session_id,
)


def _add_columns(sync_conn: Connection) -> None:
    existing = {
        str(row[1])
        for row in sync_conn.execute(sa.text("PRAGMA table_info(agent_spawned_runs)"))
    }
    for column in _COLUMNS:
        if column.name in existing:
            continue
        ddl = str(sa.schema.CreateColumn(column).compile(dialect=sync_conn.dialect))
        sync_conn.execute(sa.text(f"ALTER TABLE agent_spawned_runs ADD COLUMN {ddl}"))


async def migrate(conn: AsyncConnection) -> None:
    """Add spawned run metadata required by full Agent Network semantics."""
    await conn.run_sync(_add_columns)
