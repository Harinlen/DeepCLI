"""Tests for schedule expression parsing and next-fire computation."""

from __future__ import annotations

import time

import pytest

from kernel.schedule.schedule_parser import (
    compute_next_fire,
    human_schedule,
    parse_schedule,
)
from kernel.schedule.types import Schedule, ScheduleKind


class TestParseSchedule:
    """parse_schedule for all 4 formats."""

    # --- cron ---

    def test_cron_basic(self) -> None:
        s = parse_schedule("*/30 * * * *")
        assert s.kind == ScheduleKind.cron
        assert s.expr == "*/30 * * * *"

    def test_cron_weekday(self) -> None:
        s = parse_schedule("0 9 * * 1-5")
        assert s.kind == ScheduleKind.cron
        assert s.expr == "0 9 * * 1-5"

    # --- every ---

    def test_every_minutes(self) -> None:
        s = parse_schedule("every 30m")
        assert s.kind == ScheduleKind.every
        assert s.interval_seconds == 1800

    def test_every_hours(self) -> None:
        s = parse_schedule("every 2h")
        assert s.kind == ScheduleKind.every
        assert s.interval_seconds == 7200

    def test_every_days(self) -> None:
        s = parse_schedule("every 1d")
        assert s.kind == ScheduleKind.every
        assert s.interval_seconds == 86400

    def test_every_seconds(self) -> None:
        s = parse_schedule("every 30s")
        assert s.kind == ScheduleKind.every
        assert s.interval_seconds == 30

    def test_every_case_insensitive(self) -> None:
        s = parse_schedule("Every 5M")
        assert s.kind == ScheduleKind.every

    # --- delay (→ at) ---

    def test_delay_minutes(self) -> None:
        before = time.time()
        s = parse_schedule("5m")
        after = time.time()
        assert s.kind == ScheduleKind.at  # delay converted to at
        assert before + 300 <= s.run_at <= after + 300

    def test_delay_hours(self) -> None:
        before = time.time()
        s = parse_schedule("2h")
        assert s.kind == ScheduleKind.at
        assert s.run_at >= before + 7200

    # --- timestamp ---

    def test_iso_timestamp(self) -> None:
        s = parse_schedule("2026-05-01T09:00")
        assert s.kind == ScheduleKind.at
        assert s.run_at > 0

    def test_iso_with_timezone(self) -> None:
        s = parse_schedule("2026-05-01T09:00+08:00")
        assert s.kind == ScheduleKind.at
        assert s.run_at > 0

    # --- errors ---

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_schedule("")

    def test_garbage_raises(self) -> None:
        with pytest.raises((ValueError, ImportError)):
            parse_schedule("not a schedule")

    def test_every_too_short(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            parse_schedule("every 0s")


class TestComputeNextFire:
    """compute_next_fire for different schedule kinds."""

    def test_every(self) -> None:
        s = Schedule(kind=ScheduleKind.every, interval_seconds=60)
        now = time.time()
        nxt = compute_next_fire(s, from_time=now)
        assert abs(nxt - (now + 60)) < 0.01

    def test_at(self) -> None:
        target = time.time() + 3600
        s = Schedule(kind=ScheduleKind.at, run_at=target)
        assert compute_next_fire(s) == target

    def test_cron(self) -> None:
        s = Schedule(kind=ScheduleKind.cron, expr="* * * * *")
        now = time.time()
        nxt = compute_next_fire(s, from_time=now)
        # Next minute should be within 60 seconds
        assert 0 < nxt - now <= 60

    def test_delay_kind_raises(self) -> None:
        s = Schedule(kind=ScheduleKind.delay)
        with pytest.raises(ValueError, match="delay"):
            compute_next_fire(s)


class TestHumanSchedule:
    """human_schedule formatting."""

    def test_cron(self) -> None:
        s = Schedule(kind=ScheduleKind.cron, expr="*/5 * * * *")
        assert human_schedule(s) == "cron */5 * * * *"

    def test_every_seconds(self) -> None:
        s = Schedule(kind=ScheduleKind.every, interval_seconds=30)
        assert human_schedule(s) == "every 30s"

    def test_every_minutes(self) -> None:
        s = Schedule(kind=ScheduleKind.every, interval_seconds=300)
        assert human_schedule(s) == "every 5m"

    def test_every_hours(self) -> None:
        s = Schedule(kind=ScheduleKind.every, interval_seconds=7200)
        assert human_schedule(s) == "every 2h"

    def test_at(self) -> None:
        s = Schedule(kind=ScheduleKind.at, run_at=1746057600)
        result = human_schedule(s)
        assert result.startswith("at ")
