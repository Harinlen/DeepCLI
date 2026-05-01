"""Tests for CronScheduler maintenance loops."""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

from kernel.schedule.scheduler import CronScheduler
from kernel.schedule.store import CronStore
from kernel.schedule.types import CronExecution, CronTask, Schedule, ScheduleKind


@pytest_asyncio.fixture
async def store() -> CronStore:
    """Provide a CronStore backed by a temp SQLite database."""
    cron_store = CronStore()
    with tempfile.TemporaryDirectory() as directory:
        await cron_store.startup(Path(directory) / "test.db")
        yield cron_store  # type: ignore[misc]
        await cron_store.shutdown()


def _make_task(task_id: str = "task1") -> CronTask:
    return CronTask(
        id=task_id,
        schedule=Schedule(kind=ScheduleKind.every, interval_seconds=60),
        prompt="echo test",
        created_at=time.time(),
        next_fire_at=time.time() + 60,
    )


class _FakeSessionManager:
    """Minimal SessionManager stand-in for reaper accounting tests."""

    def __init__(self, existing_session_ids: set[str]) -> None:
        self._existing_session_ids = existing_session_ids
        self.deleted_session_ids: list[str] = []

    async def delete_session(self, session_id: str) -> bool:
        if session_id not in self._existing_session_ids:
            return False
        self._existing_session_ids.remove(session_id)
        self.deleted_session_ids.append(session_id)
        return True


class _FakeExecutor:
    """Expose the private field CronScheduler uses for reaping."""

    def __init__(self, session_manager: _FakeSessionManager) -> None:
        self._session_manager = session_manager


@pytest.mark.asyncio
async def test_session_reaper_does_not_count_missing_sessions(
    store: CronStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Old execution records should not produce repeated fake delete counts."""
    now = time.time()
    await store.add(_make_task())
    await store.add_execution(
        CronExecution(
            id="old",
            task_id="task1",
            session_id="already-gone",
            started_at=now - 2 * 86400,
        )
    )

    session_manager = _FakeSessionManager(existing_session_ids=set())
    scheduler = CronScheduler(
        store,
        _FakeExecutor(session_manager),  # type: ignore[arg-type]
        session_retention_hours=24,
    )

    with caplog.at_level(logging.INFO, logger="kernel.schedule.scheduler"):
        await scheduler._reap_sessions()

    assert session_manager.deleted_session_ids == []
    assert "Session reaper: deleted" not in caplog.text


@pytest.mark.asyncio
async def test_session_reaper_counts_existing_sessions(
    store: CronStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reaper should still report sessions it actually deletes."""
    now = time.time()
    await store.add(_make_task())
    await store.add_execution(
        CronExecution(
            id="old",
            task_id="task1",
            session_id="cron-session",
            started_at=now - 2 * 86400,
        )
    )

    session_manager = _FakeSessionManager(existing_session_ids={"cron-session"})
    scheduler = CronScheduler(
        store,
        _FakeExecutor(session_manager),  # type: ignore[arg-type]
        session_retention_hours=24,
    )

    with caplog.at_level(logging.INFO, logger="kernel.schedule.scheduler"):
        await scheduler._reap_sessions()

    assert session_manager.deleted_session_ids == ["cron-session"]
    assert "Session reaper: deleted 1 expired cron sessions" in caplog.text
