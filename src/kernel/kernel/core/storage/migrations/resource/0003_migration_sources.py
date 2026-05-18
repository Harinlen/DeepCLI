"""ResourceStore schema version 3 migration source markers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.engine import Connection

from kernel.core.storage import tables


def _create_table(sync_conn: Connection) -> None:
    tables.migration_sources.create(sync_conn, checkfirst=True)


async def migrate(conn: AsyncConnection) -> None:
    """Create legacy import marker table."""
    await conn.run_sync(_create_table)
