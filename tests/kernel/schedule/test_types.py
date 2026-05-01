"""Tests for schedule data models."""

from __future__ import annotations

import time


from kernel.schedule.types import (
    CronTask,
    CronTaskStatus,
    RepeatConfig,
    Schedule,
    ScheduleKind,
)


class TestRepeatConfig:
    """RepeatConfig.is_exhausted boundary tests."""

    def test_all_none_never_exhausted(self) -> None:
        rc = RepeatConfig()
        assert not rc.is_exhausted(9999, 0, time.time())

    def test_max_count_exact_boundary(self) -> None:
        rc = RepeatConfig(max_count=5)
        assert not rc.is_exhausted(4, 0)
        assert rc.is_exhausted(5, 0)
        assert rc.is_exhausted(6, 0)

    def test_max_count_zero(self) -> None:
        rc = RepeatConfig(max_count=0)
        assert rc.is_exhausted(0, 0)

    def test_max_duration(self) -> None:
        now = time.time()
        rc = RepeatConfig(max_duration_seconds=3600)
        assert not rc.is_exhausted(0, now, now + 3599)
        assert rc.is_exhausted(0, now, now + 3600)

    def test_until(self) -> None:
        rc = RepeatConfig(until=1_000_000)
        assert not rc.is_exhausted(0, 0, 999_999)
        assert rc.is_exhausted(0, 0, 1_000_000)

    def test_combined_any_first_wins(self) -> None:
        """When multiple limits are set, the first to trigger wins."""
        now = time.time()
        rc = RepeatConfig(max_count=3, max_duration_seconds=60)
        # Count reached first
        assert rc.is_exhausted(3, now, now + 10)
        # Duration reached first
        assert rc.is_exhausted(1, now, now + 60)
        # Neither reached
        assert not rc.is_exhausted(1, now, now + 10)


class TestCronTask:
    """CronTask dataclass basic tests."""

    def test_defaults(self) -> None:
        t = CronTask(
            id="abc",
            schedule=Schedule(kind=ScheduleKind.cron, expr="* * * * *"),
            prompt="test",
        )
        assert t.status == CronTaskStatus.active
        assert t.recurring is True
        assert t.durable is True
        assert t.fire_count == 0
        assert t.delivery.target == "session,acp"

    def test_non_durable(self) -> None:
        t = CronTask(
            id="xyz",
            schedule=Schedule(kind=ScheduleKind.every, interval_seconds=60),
            prompt="temp",
            durable=False,
        )
        assert t.durable is False


class TestSchedule:
    """Schedule value object tests."""

    def test_cron_kind(self) -> None:
        s = Schedule(kind=ScheduleKind.cron, expr="*/5 * * * *")
        assert s.kind == ScheduleKind.cron
        assert s.expr == "*/5 * * * *"

    def test_every_kind(self) -> None:
        s = Schedule(kind=ScheduleKind.every, interval_seconds=300)
        assert s.interval_seconds == 300

    def test_at_kind(self) -> None:
        s = Schedule(kind=ScheduleKind.at, run_at=1_700_000_000)
        assert s.run_at == 1_700_000_000
