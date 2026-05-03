"""Tests for DeliveryRouter."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.schedule.delivery import DeliveryRouter, _IDEMPOTENCY_MAX_ENTRIES, _IDEMPOTENCY_TTL_S
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


class TestDeliveryEdges:
    """Boundary behavior for delivery targets, retries, and cache pruning."""

    @pytest.mark.asyncio
    async def test_acp_delivery_broadcasts_to_session_senders(self) -> None:
        sent: list[str] = []

        async def _sender(payload: str) -> None:
            sent.append(payload)

        mgr = _mock_session_manager()
        mgr._sessions["sess-creator"].senders = {"ws-1": _sender}
        router = DeliveryRouter(session_manager=mgr)

        status, error = await router.deliver(_task(delivery_target="acp"), _execution())

        assert status == "delivered"
        assert error is None
        assert '"method":"session/update"' in sent[0]
        assert '"type":"cron_completion"' in sent[0]

    @pytest.mark.asyncio
    async def test_session_delivery_without_creator_session_is_successful_noop(self) -> None:
        mgr = _mock_session_manager()
        router = DeliveryRouter(session_manager=mgr)

        status, error = await router.deliver(
            _task(delivery_target="session", session_id=""),
            _execution(),
        )

        assert (status, error) == ("delivered", None)
        assert mgr._sessions["sess-creator"].pending_reminders == []

    @pytest.mark.asyncio
    async def test_gateway_delivery_routes_adapter_and_channel(self) -> None:
        gateway = MagicMock()
        gateway.send_to_channel = AsyncMock()
        router = DeliveryRouter(session_manager=_mock_session_manager(), gateway_manager=gateway)

        status, error = await router.deliver(
            _task(delivery_target="gateway:discord:channel-1"),
            _execution(summary="gateway result"),
        )

        assert (status, error) == ("delivered", None)
        gateway.send_to_channel.assert_awaited_once()
        adapter, channel, text = gateway.send_to_channel.await_args.args
        assert (adapter, channel) == ("discord", "channel-1")
        assert "gateway result" in text

    @pytest.mark.asyncio
    async def test_gateway_delivery_missing_manager_or_malformed_target_is_noop(self) -> None:
        router = DeliveryRouter(session_manager=_mock_session_manager(), gateway_manager=None)

        assert await router.deliver(_task(delivery_target="gateway:discord:ch"), _execution()) == (
            "delivered",
            None,
        )
        assert await router.deliver(_task(delivery_target="gateway:bad"), _execution()) == (
            "delivered",
            None,
        )

    @pytest.mark.asyncio
    async def test_partial_failure_is_not_cached_and_reports_first_error(self) -> None:
        gateway = MagicMock()
        gateway.send_to_channel = AsyncMock(side_effect=RuntimeError("permanent failure"))
        router = DeliveryRouter(session_manager=_mock_session_manager(), gateway_manager=gateway)
        task = _task(delivery_target="session,gateway:discord:channel-1")

        status, error = await router.deliver(task, _execution())
        retry_status, retry_error = await router.deliver(task, _execution())

        assert status == retry_status == "not-delivered"
        assert error == retry_error == "permanent failure"
        assert gateway.send_to_channel.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_transient_sleeps_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        attempts = 0
        router = DeliveryRouter(session_manager=_mock_session_manager())

        async def _sleep(delay: float) -> None:
            sleeps.append(delay)

        async def _flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("network error while sending")
            return "ok"

        monkeypatch.setattr("kernel.schedule.delivery.asyncio.sleep", _sleep)

        assert await router._retry_transient(_flaky, delays=[0.25]) == "ok"
        assert sleeps == [0.25]
        assert attempts == 2

    def test_prune_cache_removes_expired_and_oldest_entries(self) -> None:
        router = DeliveryRouter(session_manager=_mock_session_manager())
        now = time.time()
        router._delivered["expired"] = (now - _IDEMPOTENCY_TTL_S - 1, True)
        for i in range(_IDEMPOTENCY_MAX_ENTRIES + 2):
            router._delivered[f"fresh-{i}"] = (now + i, True)

        router._prune_cache()

        assert "expired" not in router._delivered
        assert len(router._delivered) == _IDEMPOTENCY_MAX_ENTRIES
        assert "fresh-0" not in router._delivered
