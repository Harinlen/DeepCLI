"""Tests for DeliveryRouter."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from kernel.schedule.delivery import DeliveryRouter
from kernel.schedule.types import (
    CronExecution,
    CronTask,
    DeliveryConfig,
    FailureAlertConfig,
    Schedule,
    ScheduleKind,
)


def _task(
    delivery_target: str = "session,acp",
    session_id: str = "sess-creator",
    **kwargs: object,
) -> CronTask:
    return CronTask(
        id="t001",
        schedule=Schedule(kind=ScheduleKind.every, interval_seconds=60),
        prompt="test",
        delivery=DeliveryConfig(target=delivery_target),
        session_id=session_id,
        created_at=time.time(),
        **kwargs,  # type: ignore[arg-type]
    )


def _execution(status: str = "completed", summary: str = "result text") -> CronExecution:
    return CronExecution(
        id="ex001",
        task_id="t001",
        session_id="sess-exec",
        started_at=time.time(),
        ended_at=time.time(),
        duration_ms=1234,
        status=status,
        summary=summary,
    )


def _mock_session_manager(session_id: str = "sess-creator") -> MagicMock:
    mgr = MagicMock()
    session = MagicMock()
    session.pending_reminders = []
    session.senders = []
    mgr._sessions = {session_id: session}
    return mgr


class TestDeliverTargets:
    """Target parsing and dispatch."""

    @pytest.mark.asyncio
    async def test_deliver_session_injects_reminder(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        task = _task(delivery_target="session")
        execution = _execution()
        status, error = await router.deliver(task, execution)

        assert status == "delivered"
        assert error is None
        session = mgr._sessions["sess-creator"]
        assert len(session.pending_reminders) == 1
        assert "t001" in session.pending_reminders[0]

    @pytest.mark.asyncio
    async def test_deliver_none_skips(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        task = _task(delivery_target="none")
        status, _ = await router.deliver(task, _execution())
        assert status == "skipped"

    @pytest.mark.asyncio
    async def test_deliver_failure_skipped_when_on_failure_false(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        task = _task(delivery_target="session")
        task.delivery.on_failure = False
        execution = _execution(status="failed")
        status, _ = await router.deliver(task, execution)
        assert status == "skipped"

    @pytest.mark.asyncio
    async def test_deliver_failure_delivered_when_on_failure_true(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        task = _task(delivery_target="session")
        task.delivery.on_failure = True
        execution = _execution(status="failed")
        status, _ = await router.deliver(task, execution)
        assert status == "delivered"


class TestSilentPattern:
    """Silent pattern suppresses delivery."""

    @pytest.mark.asyncio
    async def test_silent_pattern_match(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        task = _task(delivery_target="session")
        task.delivery.silent_pattern = r"\[SILENT\]"
        execution = _execution(summary="No changes [SILENT]")
        status, _ = await router.deliver(task, execution)
        assert status == "skipped"

    @pytest.mark.asyncio
    async def test_silent_pattern_no_match(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        task = _task(delivery_target="session")
        task.delivery.silent_pattern = r"\[SILENT\]"
        execution = _execution(summary="Something changed!")
        status, _ = await router.deliver(task, execution)
        assert status == "delivered"


class TestIdempotency:
    """Idempotency cache prevents double delivery."""

    @pytest.mark.asyncio
    async def test_second_delivery_skipped(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        task = _task(delivery_target="session")
        execution = _execution()

        s1, _ = await router.deliver(task, execution)
        assert s1 == "delivered"

        s2, _ = await router.deliver(task, execution)
        assert s2 == "delivered"  # from cache

        # But reminder was injected only once
        session = mgr._sessions["sess-creator"]
        assert len(session.pending_reminders) == 1


class TestFailureAlert:
    """Failure alert dispatching."""

    @pytest.mark.asyncio
    async def test_deliver_alert(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        task = _task(delivery_target="session")
        task.failure_alert = FailureAlertConfig(after=3, target="session")
        task.consecutive_failures = 5

        await router.deliver_alert(task, "connection refused")

        session = mgr._sessions["sess-creator"]
        assert len(session.pending_reminders) == 1
        assert "failed" in session.pending_reminders[0].lower()
