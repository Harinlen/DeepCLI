"""SQLAlchemy asyncio migration helpers shared by ResourceStore and SecretStore."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncConnection

from kernel.core.storage.errors import StoreMigrationError

logger = logging.getLogger(__name__)

Migration = tuple[int, str, Callable[[AsyncConnection], Awaitable[None]]]


async def apply_migrations(
    conn: AsyncConnection,
    *,
    store_name: str,
    schema_version: int,
    migrations: Sequence[Migration],
) -> None:
    """Apply guarded ``PRAGMA user_version`` migrations through SQLAlchemy."""
    current = await _get_version(conn)
    await conn.commit()
    if current > schema_version:
        raise StoreMigrationError(
            f"{store_name} schema version {current} is newer than this build "
            f"(expects <= {schema_version})"
        )
    if current == schema_version:
        return

    by_version = {version: (description, fn) for version, description, fn in migrations}
    for target in range(current + 1, schema_version + 1):
        item = by_version.get(target)
        if item is None:
            raise StoreMigrationError(f"{store_name} missing migration to version {target}")
        description, fn = item
        logger.info("%s: applying migration %d - %s", store_name, target, description)
        trans = await conn.begin()
        try:
            await fn(conn)
            await conn.exec_driver_sql(f"PRAGMA user_version = {target}")
            await trans.commit()
        except Exception as exc:
            await trans.rollback()
            raise StoreMigrationError(
                f"{store_name} migration to version {target} failed: {exc}"
            ) from exc


async def _get_version(conn: AsyncConnection) -> int:
    row = (await conn.exec_driver_sql("PRAGMA user_version")).fetchone()
    return int(row[0]) if row is not None else 0
