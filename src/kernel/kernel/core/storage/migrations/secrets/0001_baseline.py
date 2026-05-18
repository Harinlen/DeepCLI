"""SecretStore schema version 1 baseline."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.engine import Connection

from kernel.core.storage import tables


def _create_table(sync_conn: Connection, table) -> None:  # type: ignore[no-untyped-def]
    table.create(sync_conn, checkfirst=True)


async def migrate(conn: AsyncConnection) -> None:
    """Create baseline secret and audit tables."""
    for table in (tables.secrets, tables.secret_events):
        await conn.run_sync(_create_table, table)
