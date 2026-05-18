"""ResourceStore schema version 6 scheduled task tables."""

from __future__ import annotations

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from kernel.core.storage import tables


def _create_table(sync_conn: Connection, table) -> None:  # type: ignore[no-untyped-def]
    table.create(sync_conn, checkfirst=True)


async def migrate(conn: AsyncConnection) -> None:
    """Create ScheduleManager global truth tables."""
    for table in (
        tables.scheduled_tasks,
        tables.scheduled_task_events,
    ):
        await conn.run_sync(_create_table, table)
