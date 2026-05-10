from __future__ import annotations

import asyncio

import pytest

from kernel.agents.mustang.schedule.executor import CronExecutor
from kernel.agents.mustang.schedule.types import CronTask, Schedule, ScheduleKind


def _task(**kwargs) -> CronTask:
    task = CronTask(
        id="task-1",
        schedule=Schedule(kind=ScheduleKind.every, interval_seconds=60),
        prompt="run this",
        timeout_seconds=1,
    )
    for key, value in kwargs.items():
        setattr(task, key, value)
    return task


class _SessionManager:
    def __init__(self, *, reply: str = "done", fail: Exception | None = None, delay: float = 0) -> None:
        self.reply = reply
        self.fail = fail
        self.delay = delay
        self.created: list[tuple[str, str]] = []
        self.turns: list[tuple[str, str]] = []
        self.permission_decisions: list[str] = []

    async def create_for_gateway(self, *, instance_id: str, peer_id: str) -> str:
        self.created.append((instance_id, peer_id))
        return "session-1"

    async def run_turn_for_gateway(self, *, session_id, text, on_permission):
        self.turns.append((session_id, text))
        permission = await on_permission({"tool": "FileWrite"})
        self.permission_decisions.append(permission.decision)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        return self.reply


class _DeliveryRouter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[CronTask, object]] = []

    async def deliver(self, task: CronTask, execution):
        self.calls.append((task, execution))
        if self.fail:
            raise RuntimeError("delivery failed")
        return ("delivered", None)


class _Hooks:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[object] = []

    async def fire(self, ctx) -> None:
        self.events.append(ctx.event)
        if self.fail:
            raise RuntimeError("hook failed")
        if ctx.event.value == "pre_cron_fire":
            ctx.messages.append("hook context")


async def test_executor_success_enriches_prompt_auto_approves_and_delivers() -> None:
    session = _SessionManager(reply="answer")
    delivery = _DeliveryRouter()
    hooks = _Hooks()

    execution = await CronExecutor(session, delivery, hooks=hooks).execute(_task())

    assert execution.status == "completed"
    assert execution.session_id == "session-1"
    assert execution.summary == "answer"
    assert execution.stop_reason == "end_turn"
    assert execution.delivery_status == "delivered"
    assert session.created == [("cron:task-1", "cron-executor")]
    assert session.permission_decisions == ["allow_once"]
    assert session.turns[0][1].startswith("[Pre-run data]\nhook context")
    assert len(delivery.calls) == 1


async def test_executor_timeout_records_timeout() -> None:
    session = _SessionManager(delay=0.05)

    execution = await CronExecutor(session).execute(_task(timeout_seconds=0.001))

    assert execution.status == "timeout"
    assert "timed out" in (execution.error or "")


async def test_executor_failure_records_exception_and_still_attempts_delivery() -> None:
    session = _SessionManager(fail=RuntimeError("turn exploded"))
    delivery = _DeliveryRouter()

    execution = await CronExecutor(session, delivery).execute(_task())

    assert execution.status == "failed"
    assert execution.error == "turn exploded"
    assert execution.delivery_status == "delivered"


async def test_executor_ignores_hook_and_delivery_failures() -> None:
    session = _SessionManager(reply="answer")
    delivery = _DeliveryRouter(fail=True)
    hooks = _Hooks(fail=True)

    execution = await CronExecutor(session, delivery, hooks=hooks).execute(_task())

    assert execution.status == "completed"
    assert execution.delivery_status == "not-delivered"
    assert session.turns == [("session-1", "run this")]


async def test_heartbeat_loop_calls_heartbeat_until_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def heartbeat(task_id: str) -> None:
        calls.append(task_id)
        raise asyncio.CancelledError

    async def fast_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("kernel.agents.mustang.schedule.executor.asyncio.sleep", fast_sleep)

    await CronExecutor(_SessionManager(), heartbeat_fn=heartbeat)._heartbeat_loop("task-1")

    assert calls == ["task-1"]
