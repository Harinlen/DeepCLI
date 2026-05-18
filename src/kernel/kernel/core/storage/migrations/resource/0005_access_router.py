"""ResourceStore schema version 5 Access Router adapter tables."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.engine import Connection

from kernel.core.storage import tables


def _create_table(sync_conn: Connection, table) -> None:  # type: ignore[no-untyped-def]
    table.create(sync_conn, checkfirst=True)


async def migrate(conn: AsyncConnection) -> None:
    """Create Access Router durable adapter/binding/idempotency tables."""
    for table in (
        tables.access_adapters,
        tables.access_adapter_events,
        tables.access_channel_bindings,
        tables.access_idempotency_keys,
    ):
        await conn.run_sync(_create_table, table)
