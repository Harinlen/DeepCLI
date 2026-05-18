"""ResourceStore schema version 4 AgentManager tables."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.engine import Connection

from kernel.core.storage import tables


def _create_table(sync_conn: Connection, table) -> None:  # type: ignore[no-untyped-def]
    table.create(sync_conn, checkfirst=True)


async def migrate(conn: AsyncConnection) -> None:
    """Create AgentManager durable control-plane tables."""
    for table in (
        tables.agent_definitions,
        tables.agent_bindings,
        tables.agent_directory_meta,
        tables.agent_runtime_status,
        tables.management_grants,
    ):
        await conn.run_sync(_create_table, table)
    await conn.execute(
        sa.insert(tables.agent_directory_meta)
        .values(key="revision", value=0)
        .prefix_with("OR IGNORE")
    )
