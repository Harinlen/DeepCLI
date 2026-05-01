"""Automatic SQLite schema migrations for the session store.

Uses ``PRAGMA user_version`` — a free integer stored in the SQLite DB header —
to track which migrations have been applied.  No extra tables are needed.

How it works
------------
``SCHEMA_VERSION`` is the highest version this build understands.

On every ``SessionStore.open()``:

1. ``Base.metadata.create_all`` creates all tables on a fresh install
   (no-op if they already exist).
2. ``apply()`` reads ``PRAGMA user_version``.
3. **Version 0 (fresh DB)**: ``create_all`` already did the right thing;
   stamp it with ``SCHEMA_VERSION`` and return.
4. **Version < SCHEMA_VERSION (upgrade)**: run each pending migration in
   order.  Each migration + its version bump run inside the same
   ``BEGIN … COMMIT`` block — if a migration fails the version is not
   advanced and the next startup will retry it.
5. **Version == SCHEMA_VERSION**: nothing to do.

Adding a migration
------------------
1. Write a ``async def _migrate_to_N(conn: AsyncConnection) -> None`` function.
2. Append ``(N, "short description", _migrate_to_N)`` to ``_MIGRATIONS``.
3. Bump ``SCHEMA_VERSION = N``.

The list MUST remain in ascending version order.  Never delete or reorder
entries — the version numbers are permanent.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import sqlalchemy as sa

from kernel.session.models import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 2
"""The schema version this codebase expects.

**Convention**: ``SCHEMA_VERSION`` always equals the kernel *major* version.
Every schema change requires a kernel major bump; every kernel major bump
implies a schema change (or a fundamental architecture restructure).

    SCHEMA_VERSION 1  →  kernel 1.x.x   (SQLite session storage, initial)
    SCHEMA_VERSION 2  →  kernel 2.x.x   (session archive + title source)

Increment this value *and* ``kernel.__version__`` major together.
"""

MigrationFn = Callable[["AsyncConnection"], Awaitable[None]]


# (target_version, description, migration_fn).  ``fn=None`` means the schema
# at that version is what ``Base.metadata.create_all`` produces — only valid
# for version 1, the initial schema.  Append new entries; never reorder.
async def _migrate_to_2(conn: "AsyncConnection") -> None:
    """Add archive and title-source metadata to existing session DBs."""
    await conn.execute(sa.text("ALTER TABLE sessions ADD COLUMN archived_at TEXT"))
    await conn.execute(sa.text("ALTER TABLE sessions ADD COLUMN title_source TEXT"))


_MIGRATIONS: list[tuple[int, str, MigrationFn | None]] = [
    (1, "initial schema (sessions + session_events)", None),
    (2, "session archive metadata + title source", _migrate_to_2),
]


async def apply(engine: "AsyncEngine") -> None:
    """Create tables and apply any pending migrations.

    Safe to call on every startup: idempotent when already at
    ``SCHEMA_VERSION``.

    Args:
        engine: Open ``AsyncEngine`` connected to ``sessions.db``.

    Raises:
        RuntimeError: If the on-disk version is newer than ``SCHEMA_VERSION``
            (i.e. the binary is older than the DB).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    current = await _get_version(engine)

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"sessions.db schema version {current} is newer than this "
            f"build (expects ≤ {SCHEMA_VERSION}).  Upgrade the kernel."
        )

    if current == SCHEMA_VERSION:
        return

    if current == 0:
        await _set_version(engine, SCHEMA_VERSION)
        logger.info("sessions.db: new database, schema version set to %d", SCHEMA_VERSION)
        return

    for to_ver, desc, fn in _MIGRATIONS:
        if current >= to_ver:
            continue
        logger.info("sessions.db: applying migration %d — %s", to_ver, desc)
        if fn is not None:
            async with engine.begin() as conn:
                await fn(conn)
                # Bump version inside the same transaction: a crash mid-migration
                # leaves user_version unchanged so the next startup retries.
                await conn.execute(sa.text(f"PRAGMA user_version = {to_ver}"))
        else:
            await _set_version(engine, to_ver)
        current = to_ver
        logger.info("sessions.db: schema version advanced to %d", current)


async def _get_version(engine: "AsyncEngine") -> int:
    """Read ``PRAGMA user_version`` from the database."""
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("PRAGMA user_version"))
        value = result.scalar()
        return int(value) if value is not None else 0


async def _set_version(engine: "AsyncEngine", version: int) -> None:
    """Write ``PRAGMA user_version`` to the database.

    PRAGMA cannot be parameterised; the version comes from the
    ``_MIGRATIONS`` table so string formatting is safe here.
    """
    async with engine.begin() as conn:
        await conn.execute(sa.text(f"PRAGMA user_version = {version}"))
