"""SQLAlchemy asyncio helpers for Kernel SQLite stores."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine, Iterator, Mapping
from pathlib import Path
from typing import TypeVar

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker, create_async_engine

from kernel.core.storage.errors import StoreBusyTimeout, StoreOpenError

T = TypeVar("T")


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def run_async(coro_factory: Callable[[], Coroutine[object, object, T]]) -> T:
    """Run one async SQLAlchemy operation from sync Kernel APIs."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: list[T] = []
    error: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(asyncio.run(coro_factory()))
        except BaseException as exc:  # pragma: no cover - re-raised below.
            error.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def make_engine(path: Path) -> AsyncEngine:
    engine = create_async_engine(sqlite_url(path), connect_args={"timeout": 5.0})

    @event.listens_for(engine.sync_engine, "connect")
    def _configure(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


def make_sessionmaker(path: Path):
    """Create an async sessionmaker for store code that needs ORM-style units."""
    return async_sessionmaker(make_engine(path), expire_on_commit=False)


async def apply_store_migrations(
    conn: AsyncConnection,
    *,
    store_name: str,
    schema_version: int,
    migrations,
) -> None:
    from kernel.core.storage.migrations import apply_migrations

    try:
        await apply_migrations(
            conn,
            store_name=store_name,
            schema_version=schema_version,
            migrations=migrations,
        )
    except sa.exc.OperationalError as exc:
        raise map_operational_error(exc) from exc


def map_operational_error(exc: BaseException) -> StoreOpenError | StoreBusyTimeout:
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return StoreBusyTimeout(str(exc))
    return StoreOpenError(str(exc))


class SyncConnectionAdapter:
    """Compatibility adapter for legacy store callbacks.

    New production code should prefer SQLAlchemy Core operations directly.
    This adapter exists so older manager/config code can be migrated in slices
    while the physical connection is still owned by SQLAlchemy asyncio.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def execute(self, statement, parameters=None):  # type: ignore[no-untyped-def]
        if isinstance(statement, str):
            return SyncResultAdapter(self._conn.exec_driver_sql(statement, parameters))
        return SyncResultAdapter(self._conn.execute(statement, parameters or {}))

    def scalar(self, statement, parameters=None):  # type: ignore[no-untyped-def]
        result = self.execute(statement, parameters)
        return result.scalar()


class SyncResultAdapter:
    def __init__(self, result) -> None:  # type: ignore[no-untyped-def]
        self._result = result
        self.lastrowid = getattr(result, "lastrowid", None)

    def fetchone(self):  # type: ignore[no-untyped-def]
        row = self._result.fetchone()
        return _row_mapping(row) if row is not None else None

    def fetchall(self):  # type: ignore[no-untyped-def]
        return [_row_mapping(row) for row in self._result.fetchall()]

    def scalar(self):  # type: ignore[no-untyped-def]
        return self._result.scalar()

    @property
    def rowcount(self) -> int:
        return int(getattr(self._result, "rowcount", -1))


def _row_mapping(row):  # type: ignore[no-untyped-def]
    return RowAdapter(row)


class RowAdapter(Mapping):
    """Row-shaped wrapper for legacy verification callbacks."""

    def __init__(self, row) -> None:  # type: ignore[no-untyped-def]
        self._row = row
        self._mapping = row._mapping
        self._string_mapping = {
            _string_key(key): value for key, value in self._mapping.items()
        }

    def __getitem__(self, key):  # type: ignore[no-untyped-def]
        if isinstance(key, int):
            return self._row[key]
        if isinstance(key, str) and key in self._string_mapping:
            return self._string_mapping[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._row)

    def __len__(self) -> int:
        return len(self._string_mapping)

    def keys(self):  # type: ignore[no-untyped-def]
        return self._string_mapping.keys()

    def items(self):  # type: ignore[no-untyped-def]
        return self._string_mapping.items()


def _string_key(key) -> str:  # type: ignore[no-untyped-def]
    if isinstance(key, str):
        return str(key)
    candidate = getattr(key, "key", None) or getattr(key, "name", None)
    return str(candidate or key)


async def run_sync_tx(
    db_path: Path,
    fn: Callable[[SyncConnectionAdapter], T],
    *,
    immediate: bool,
) -> T:
    engine = make_engine(db_path)
    try:
        async with engine.connect() as async_conn:
            if immediate:
                await async_conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                await async_conn.begin()
            try:
                result = await async_conn.run_sync(lambda conn: fn(SyncConnectionAdapter(conn)))
                await async_conn.commit()
                return result
            except Exception:
                await async_conn.rollback()
                raise
    except sa.exc.OperationalError as exc:
        raise map_operational_error(exc) from exc
    finally:
        await engine.dispose()
