"""ResourceStore schema version 2 config and flag tables."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.engine import Connection

from kernel.core.storage import tables


def _create_table(sync_conn: Connection, table) -> None:  # type: ignore[no-untyped-def]
    table.create(sync_conn, checkfirst=True)


async def migrate(conn: AsyncConnection) -> None:
    """Create config/flag section tables."""
    for table in (
        tables.config_sections,
        tables.config_events,
        tables.flag_sections,
        tables.flag_events,
    ):
        await conn.run_sync(_create_table, table)
