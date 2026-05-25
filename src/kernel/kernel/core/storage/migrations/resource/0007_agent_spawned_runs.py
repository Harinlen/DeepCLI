"""ResourceStore schema version 7 Agent spawned run tables."""

from __future__ import annotations

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from kernel.core.storage import tables


def _create_table(sync_conn: Connection, table) -> None:  # type: ignore[no-untyped-def]
    table.create(sync_conn, checkfirst=True)


async def migrate(conn: AsyncConnection) -> None:
    """Create durable Agent spawned run registry tables."""
    for table in (
        tables.agent_spawned_runs,
        tables.agent_spawned_run_events,
    ):
        await conn.run_sync(_create_table, table)
