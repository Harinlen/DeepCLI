"""Tests for the automatic SQLite schema migration system.

Covers:
- Fresh DB gets stamped with SCHEMA_VERSION on first open.
- Re-opening an up-to-date DB is a no-op.
- A DB at an older version (< SCHEMA_VERSION) gets migrated step by step.
- A DB at a newer version raises RuntimeError (binary too old).
- Migration failure leaves version unchanged (retry on next startup).
- Each migration function runs exactly once even when open() is called
  multiple times.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from kernel.session import migrations as mig
from kernel.session.migrations import SCHEMA_VERSION, _get_version, _set_version

# Mark every async test in this module to run under anyio (asyncio backend).
pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _open_engine(db_path: Path):  # type: ignore[return]
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fresh-install path
# ---------------------------------------------------------------------------


async def test_fresh_db_gets_schema_version(tmp_path: Path) -> None:
    """A brand-new DB should be stamped with SCHEMA_VERSION after apply()."""
    db = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)
    try:
        await mig.apply(engine)
        version = await _get_version(engine)
        assert version == SCHEMA_VERSION
    finally:
        await engine.dispose()


async def test_fresh_db_creates_tables(tmp_path: Path) -> None:
    """Tables must exist after apply() on a fresh DB."""
    from kernel.session.models import ConversationRecord

    db = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)
    try:
        await mig.apply(engine)
        # If the table doesn't exist, this SELECT raises.
        async with engine.connect() as conn:
            await conn.execute(sa.select(ConversationRecord))
    finally:
        await engine.dispose()


async def test_fresh_db_creates_archive_columns(tmp_path: Path) -> None:
    """Fresh schema includes archive and title-source columns."""
    db = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)
    try:
        await mig.apply(engine)
        async with engine.connect() as conn:
            rows = await conn.execute(sa.text("PRAGMA table_info(sessions)"))
            columns = {row[1] for row in rows.fetchall()}
        assert {"archived_at", "title_source"} <= columns
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Idempotent re-open
# ---------------------------------------------------------------------------


async def test_apply_idempotent_at_current_version(tmp_path: Path) -> None:
    """Calling apply() again on an already-migrated DB is a no-op."""
    db = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)
    try:
        await mig.apply(engine)
        v_before = await _get_version(engine)

        await mig.apply(engine)
        v_after = await _get_version(engine)

        assert v_before == v_after == SCHEMA_VERSION
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Upgrade path (simulated)
# ---------------------------------------------------------------------------


async def test_pending_migration_is_applied(tmp_path: Path) -> None:
    """A DB one version behind should be advanced when apply() runs."""
    db = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)

    calls: list[int] = []

    async def _fake_migration(conn: AsyncConnection) -> None:
        calls.append(1)

    # Temporarily inject a migration at SCHEMA_VERSION + 1.
    target = SCHEMA_VERSION + 1
    original_migrations = mig._MIGRATIONS[:]
    original_version = mig.SCHEMA_VERSION

    try:
        # Bootstrap the DB at the current real version.
        await mig.apply(engine)

        # Inject a fake next-version migration.
        mig._MIGRATIONS.append((target, "fake test migration", _fake_migration))
        mig.SCHEMA_VERSION = target  # type: ignore[assignment]

        # apply() should run the new migration.
        await mig.apply(engine)

        assert calls == [1], "migration function must be called exactly once"
        assert await _get_version(engine) == target
    finally:
        # Restore original state so other tests are unaffected.
        mig._MIGRATIONS.clear()
        mig._MIGRATIONS.extend(original_migrations)
        mig.SCHEMA_VERSION = original_version  # type: ignore[assignment]
        await engine.dispose()


async def test_migration_not_reapplied_on_second_open(tmp_path: Path) -> None:
    """A migration must run only once; subsequent opens must skip it."""
    db = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)

    calls: list[int] = []

    async def _fake_migration(conn: AsyncConnection) -> None:
        calls.append(1)

    target = SCHEMA_VERSION + 1
    original_migrations = mig._MIGRATIONS[:]
    original_version = mig.SCHEMA_VERSION

    try:
        await mig.apply(engine)  # bootstrap

        mig._MIGRATIONS.append((target, "fake test migration", _fake_migration))
        mig.SCHEMA_VERSION = target  # type: ignore[assignment]

        await mig.apply(engine)  # first: should run
        await mig.apply(engine)  # second: must not re-run

        assert calls == [1], "migration must run exactly once"
    finally:
        mig._MIGRATIONS.clear()
        mig._MIGRATIONS.extend(original_migrations)
        mig.SCHEMA_VERSION = original_version  # type: ignore[assignment]
        await engine.dispose()


# ---------------------------------------------------------------------------
# Future-version guard
# ---------------------------------------------------------------------------


async def test_newer_db_raises(tmp_path: Path) -> None:
    """A DB whose version exceeds SCHEMA_VERSION must raise RuntimeError."""
    db = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)
    try:
        # Stamp the DB with a version higher than what this build knows.
        await mig.apply(engine)
        await _set_version(engine, SCHEMA_VERSION + 99)

        with pytest.raises(RuntimeError, match="newer than this build"):
            await mig.apply(engine)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Failed migration leaves version unchanged
# ---------------------------------------------------------------------------


async def test_failed_migration_does_not_advance_version(tmp_path: Path) -> None:
    """If a migration fn raises, the version must remain unchanged."""
    db = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)

    async def _bad_migration(conn: AsyncConnection) -> None:
        raise ValueError("simulated migration failure")

    target = SCHEMA_VERSION + 1
    original_migrations = mig._MIGRATIONS[:]
    original_version = mig.SCHEMA_VERSION

    try:
        await mig.apply(engine)  # bootstrap
        version_before = await _get_version(engine)

        mig._MIGRATIONS.append((target, "bad migration", _bad_migration))
        mig.SCHEMA_VERSION = target  # type: ignore[assignment]

        with pytest.raises(Exception):
            await mig.apply(engine)

        version_after = await _get_version(engine)
        assert version_after == version_before, "version must not advance when a migration fails"
    finally:
        mig._MIGRATIONS.clear()
        mig._MIGRATIONS.extend(original_migrations)
        mig.SCHEMA_VERSION = original_version  # type: ignore[assignment]
        await engine.dispose()


# ---------------------------------------------------------------------------
# SessionStore integration — migration runs inside open()
# ---------------------------------------------------------------------------


async def test_store_open_applies_migrations(tmp_path: Path) -> None:
    """SessionStore.open() must stamp the version on a fresh DB."""
    from kernel.session.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    await store.open()
    version = await _get_version(store._engine)  # type: ignore[arg-type]
    assert version == SCHEMA_VERSION
    await store.close()
