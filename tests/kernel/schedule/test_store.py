"""Tests for CronStore SQLite persistence."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

from kernel.schedule.store import CronStore
from kernel.schedule.types import (
    CronExecution,
    CronTask,
    CronTaskStatus,
    DeliveryConfig,
    RepeatConfig,
    Schedule,
    ScheduleKind,
)


@pytest_asyncio.fixture
async def store() -> CronStore:
    """Provide a CronStore backed by a temp SQLite database."""
    s = CronStore()
    with tempfile.TemporaryDirectory() as d:
        await s.startup(Path(d) / "test.db")
        yield s  # type: ignore[misc]
        await s.shutdown()


def _make_task(
    task_id: str = "t001",
    *,
    durable: bool = True,
    status: CronTaskStatus = CronTaskStatus.active,
) -> CronTask:
    return CronTask(
        id=task_id,
        schedule=Schedule(kind=ScheduleKind.every, interval_seconds=60),
        prompt="echo test",
        durable=durable,
        status=status,
        created_at=time.time(),
        next_fire_at=time.time() + 60,
    )


class TestCRUD:
    """Basic CRUD operations."""

    @pytest.mark.asyncio
    async def test_add_and_get(self, store: CronStore) -> None:
        task = _make_task()
        await store.add(task)
        got = await store.get("t001")
        assert got is not None
        assert got.id == "t001"
        assert got.prompt == "echo test"
        assert got.schedule.interval_seconds == 60

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store: CronStore) -> None:
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_active(self, store: CronStore) -> None:
        await store.add(_make_task("a1"))
        await store.add(_make_task("a2"))
        await store.add(_make_task("a3", status=CronTaskStatus.paused))
        active = await store.list_active()
        ids = {t.id for t in active}
        assert "a1" in ids
        assert "a2" in ids
        assert "a3" not in ids

    @pytest.mark.asyncio
    async def test_remove_soft_deletes(self, store: CronStore) -> None:
        await store.add(_make_task())
        ok = await store.remove("t001")
        assert ok
        got = await store.get("t001")
        assert got is not None
        assert got.status == CronTaskStatus.deleted

    @pytest.mark.asyncio
    async def test_remove_missing(self, store: CronStore) -> None:
        assert not await store.remove("ghost")

    @pytest.mark.asyncio
    async def test_update_fired(self, store: CronStore) -> None:
        await store.add(_make_task())
        now = time.time()
        await store.update_fired("t001", now, now + 60)
        got = await store.get("t001")
        assert got is not None
        assert got.fire_count == 1
        assert got.consecutive_failures == 0
        assert got.last_fired_at == now

    @pytest.mark.asyncio
    async def test_update_status(self, store: CronStore) -> None:
        await store.add(_make_task())
        await store.update_status(
            "t001", CronTaskStatus.paused, next_fire_at=None
        )
        got = await store.get("t001")
        assert got is not None
        assert got.status == CronTaskStatus.paused
        assert got.next_fire_at is None


class TestNonDurable:
    """Non-durable (memory-only) task behaviour."""

    @pytest.mark.asyncio
    async def test_non_durable_in_memory(self, store: CronStore) -> None:
        task = _make_task("mem1", durable=False)
        await store.add(task)
        got = await store.get("mem1")
        assert got is not None
        assert got.id == "mem1"

    @pytest.mark.asyncio
    async def test_non_durable_in_active_list(self, store: CronStore) -> None:
        await store.add(_make_task("mem2", durable=False))
        active = await store.list_active()
        assert any(t.id == "mem2" for t in active)

    @pytest.mark.asyncio
    async def test_non_durable_remove(self, store: CronStore) -> None:
        await store.add(_make_task("mem3", durable=False))
        ok = await store.remove("mem3")
        assert ok
        assert await store.get("mem3") is None


class TestExecutionRecords:
    """CronExecution persistence."""

    @pytest.mark.asyncio
    async def test_add_and_list(self, store: CronStore) -> None:
        await store.add(_make_task())
        ex = CronExecution(
            id="ex01", task_id="t001", session_id="s1", started_at=time.time()
        )
        await store.add_execution(ex)
        execs = await store.list_executions("t001")
        assert len(execs) == 1
        assert execs[0].id == "ex01"

    @pytest.mark.asyncio
    async def test_list_ordered_by_recency(self, store: CronStore) -> None:
        await store.add(_make_task())
        now = time.time()
        for i in range(3):
            await store.add_execution(
                CronExecution(
                    id=f"ex{i}",
                    task_id="t001",
                    session_id=f"s{i}",
                    started_at=now + i,
                )
            )
        execs = await store.list_executions("t001")
        assert [e.id for e in execs] == ["ex2", "ex1", "ex0"]

    @pytest.mark.asyncio
    async def test_prune(self, store: CronStore) -> None:
        await store.add(_make_task())
        old_time = time.time() - 100 * 86400  # 100 days ago
        await store.add_execution(
            CronExecution(
                id="old", task_id="t001", session_id="s", started_at=old_time
            )
        )
        await store.add_execution(
            CronExecution(
                id="new",
                task_id="t001",
                session_id="s",
                started_at=time.time(),
            )
        )
        deleted = await store.prune_executions(retention_days=30)
        assert deleted == 1
        execs = await store.list_executions("t001")
        assert len(execs) == 1
        assert execs[0].id == "new"


class TestRoundTrip:
    """Verify all CronTask fields survive a SQLite round-trip."""

    @pytest.mark.asyncio
    async def test_full_field_roundtrip(self, store: CronStore) -> None:
        from kernel.schedule.types import FailureAlertConfig

        task = CronTask(
            id="rt001",
            schedule=Schedule(
                kind=ScheduleKind.cron, expr="0 9 * * 1-5"
            ),
            prompt="generate report",
            description="Daily 9am report",
            recurring=True,
            durable=True,
            skills=["/check-build", "/deploy"],
            model="claude-sonnet-4-6",
            timeout_seconds=600,
            inactivity_timeout_seconds=120,
            delivery=DeliveryConfig(
                target="gateway:discord:123",
                on_failure=False,
                silent_pattern="\\[SILENT\\]",
            ),
            session_id="sess-creator",
            project_dir="/home/user/project",
            created_at=1700000000,
            last_fired_at=1700003600,
            next_fire_at=1700007200,
            status=CronTaskStatus.active,
            fire_count=42,
            consecutive_failures=2,
            max_age_seconds=86400,
            repeat=RepeatConfig(
                max_count=100,
                max_duration_seconds=604800,
                until=1800000000,
            ),
            failure_alert=FailureAlertConfig(
                after=5, cooldown_seconds=7200, target="gateway:discord:456"
            ),
            last_failure_alert_at=1700003000,
        )
        await store.add(task)
        got = await store.get("rt001")
        assert got is not None

        assert got.schedule.kind == ScheduleKind.cron
        assert got.schedule.expr == "0 9 * * 1-5"
        assert got.prompt == "generate report"
        assert got.description == "Daily 9am report"
        assert got.skills == ["/check-build", "/deploy"]
        assert got.model == "claude-sonnet-4-6"
        assert got.timeout_seconds == 600
        assert got.inactivity_timeout_seconds == 120
        assert got.delivery.target == "gateway:discord:123"
        assert got.delivery.on_failure is False
        assert got.delivery.silent_pattern == "\\[SILENT\\]"
        assert got.session_id == "sess-creator"
        assert got.project_dir == "/home/user/project"
        assert got.fire_count == 42
        assert got.consecutive_failures == 2
        assert got.max_age_seconds == 86400
        assert got.repeat.max_count == 100
        assert got.repeat.max_duration_seconds == 604800
        assert got.repeat.until == 1800000000
        assert got.failure_alert is not None
        assert got.failure_alert.after == 5
        assert got.failure_alert.cooldown_seconds == 7200
        assert got.failure_alert.target == "gateway:discord:456"
        assert got.last_failure_alert_at == 1700003000
