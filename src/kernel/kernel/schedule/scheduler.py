"""CronScheduler — event-driven timer that fires cron tasks.

Design reference: ``docs/plans/schedule-manager.md`` § 3.3.

The scheduler runs as a single ``asyncio.Task`` for the lifetime of
the kernel.  Instead of polling every N seconds, it sleeps until the
next task is due (capped at 60 s to catch clock drift and stale
claims).  ``notify_change()`` wakes it early when the task list
changes.

Multi-instance safety
---------------------
Multiple kernel instances may share the same ``kernel.db``.  The
scheduler uses a ``running_by`` / ``running_heartbeat`` claim
protocol (SQLite row-level CAS) to prevent duplicate fires.
Stale claims (heartbeat > 2 min old) are cleaned up every tick.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

from kernel.schedule.schedule_parser import compute_next_fire
from kernel.schedule.types import CronTask, CronTaskStatus

if TYPE_CHECKING:
    from kernel.schedule.executor import CronExecutor
    from kernel.schedule.store import CronStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SLEEP_S: float = 60.0
"""Cap sleep to 60 s — catches clock drift and lets us periodically
clean up stale claims from crashed kernel instances."""

HEARTBEAT_INTERVAL_S: float = 30.0
"""How often the executor refreshes ``running_heartbeat``."""

HEARTBEAT_STALE_THRESHOLD_S: float = 120.0
"""A claim with heartbeat older than this is considered abandoned."""

STARTUP_STAGGER_S: float = 5.0
"""Delay between missed-task catch-up fires (prevents overload)."""

MAX_IMMEDIATE_CATCHUP: int = 5
"""Fire at most this many missed tasks immediately; stagger the rest."""


class CronScheduler:
    """Event-driven cron timer with multi-instance claim safety."""

    def __init__(
        self,
        store: CronStore,
        executor: CronExecutor,
        *,
        max_concurrent: int = 1,
        session_retention_hours: int = 24,
        execution_retention_days: int = 30,
    ) -> None:
        self._store = store
        self._executor = executor
        self._max_concurrent = max_concurrent
        self._session_retention_hours = session_retention_hours
        self._execution_retention_days = execution_retention_days
        self._kernel_id: str = uuid.uuid4().hex[:16]
        self._task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._running = False
        self._last_reap_time: float = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _heartbeat_fn_for_executor(self) -> object:
        """Return a callable for CronExecutor's heartbeat loop."""

        async def _heartbeat(task_id: str) -> None:
            db = self._store.db
            await db.execute(
                "UPDATE cron_tasks SET running_heartbeat = ? WHERE id = ? AND running_by = ?",
                (time.time(), task_id, self._kernel_id),
            )
            await db.commit()

        return _heartbeat

    async def start(self) -> None:
        """Launch the scheduler background task."""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._schedule_loop(), name="cron-scheduler")
        logger.info("CronScheduler started (kernel_id=%s)", self._kernel_id)

    async def stop(self) -> None:
        """Cancel the scheduler and wait for it to finish."""
        self._running = False
        self._wake_event.set()  # unblock sleep
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("CronScheduler stopped")

    async def notify_change(self) -> None:
        """Wake the scheduler early (e.g. after task creation)."""
        self._wake_event.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _schedule_loop(self) -> None:
        """The core scheduler loop.

        Steps per tick:
        1. Clean up stale claims (crashed kernel instances)
        2. Load active tasks from store
        3. Find soonest ``next_fire_at``
        4. Sleep until then (capped at 60 s), or until woken
        5. Claim due tasks (CAS on ``running_by``)
        6. Fire claimed tasks (respecting concurrency limit)
        7. Handle expiry / repeat exhaustion
        8. Repeat
        """
        try:
            while self._running:
                await self._cleanup_stale_claims()

                active = await self._store.list_active()
                now = time.time()

                # Find due tasks and soonest future fire
                due: list[CronTask] = []
                soonest: float | None = None
                for task in active:
                    if task.next_fire_at is None:
                        continue
                    if task.running_by is not None:
                        # Already claimed by some kernel instance
                        continue
                    if task.next_fire_at <= now:
                        due.append(task)
                    else:
                        if soonest is None or task.next_fire_at < soonest:
                            soonest = task.next_fire_at

                # Check max_age expiry for recurring tasks
                for task in active:
                    if (
                        task.recurring
                        and task.max_age_seconds > 0
                        and task.last_fired_at is not None
                        and now - task.last_fired_at > task.max_age_seconds
                    ):
                        logger.info(
                            "Cron task %s expired (no activity for %.0f days)",
                            task.id,
                            task.max_age_seconds / 86400,
                        )
                        await self._store.update_status(
                            task.id, CronTaskStatus.expired, next_fire_at=None
                        )

                # Fire due tasks
                if due:
                    await self._fire_batch(due)

                # Session reaper (every 5 min) + execution prune
                if now - self._last_reap_time > 300:
                    self._last_reap_time = now
                    await self._reap_sessions()
                    if self._execution_retention_days > 0:
                        pruned = await self._store.prune_executions(self._execution_retention_days)
                        if pruned:
                            logger.info("Pruned %d old execution records", pruned)

                # Sleep until next fire or MAX_SLEEP_S
                if soonest is not None:
                    delay = min(soonest - time.time(), MAX_SLEEP_S)
                else:
                    delay = MAX_SLEEP_S
                delay = max(delay, 0.1)  # never negative

                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass  # normal — just time to check again

        except asyncio.CancelledError:
            logger.debug("CronScheduler loop cancelled")
        except Exception:
            logger.exception("CronScheduler loop crashed")

    # ------------------------------------------------------------------
    # Fire
    # ------------------------------------------------------------------

    async def _fire_batch(self, tasks: list[CronTask]) -> None:
        """Claim and fire a batch of due tasks."""
        # Claim via SQLite CAS
        claimed = await self._claim_tasks(tasks)
        if not claimed:
            return

        # Fire with concurrency limit
        sem = asyncio.Semaphore(self._max_concurrent)

        async def _run(task: CronTask) -> None:
            async with sem:
                await self._fire_task(task)

        await asyncio.gather(*[_run(t) for t in claimed], return_exceptions=True)

    async def _fire_task(self, task: CronTask) -> None:
        """Execute a single cron task and update state."""
        try:
            execution = await self._executor.execute(task)

            # Persist execution record
            try:
                await self._store.add_execution(execution)
            except Exception:
                logger.exception("Failed to save execution record for %s", task.id)

            # Update state based on result
            now = time.time()
            if execution.status in ("completed", "running"):
                # Successful execution
                if task.recurring:
                    next_at = compute_next_fire(task.schedule, from_time=now)
                    await self._store.update_fired(task.id, now, next_at)

                    # Check repeat exhaustion
                    new_count = task.fire_count + 1
                    if task.repeat.is_exhausted(new_count, task.created_at, now):
                        await self._store.update_status(
                            task.id,
                            CronTaskStatus.completed,
                            next_fire_at=None,
                        )
                else:
                    # One-shot — mark completed
                    await self._store.update_fired(task.id, now, None)
                    await self._store.update_status(
                        task.id, CronTaskStatus.completed, next_fire_at=None
                    )
            else:
                # Failed — delegate to failure handler
                await self._handle_failure(task, execution.error or "unknown error")

        except Exception as exc:
            logger.exception("Failed to fire task %s", task.id)
            await self._handle_failure(task, str(exc))
        finally:
            await self._release_task(task.id)

    async def _handle_failure(self, task: CronTask, error: str) -> None:
        """Apply backoff logic for a failed execution.

        Full OpenClaw-mode failure handling — see § 3.4 of design doc.
        Recurring tasks use max(natural_next, backoff), one-shot tasks
        retry up to 3 times on transient errors then pause.
        """
        from kernel.schedule.errors import (
            ONESHOT_MAX_TRANSIENT_RETRIES,
            backoff_delay,
            is_transient_error,
        )

        new_failures = task.consecutive_failures + 1
        now = time.time()

        if task.recurring:
            # Recurring: backoff but keep going
            try:
                natural_next = compute_next_fire(task.schedule, from_time=now)
            except Exception:
                # Schedule computation failure — 3 strikes and out
                logger.warning(
                    "Schedule compute error for task %s (failures=%d)",
                    task.id,
                    new_failures,
                )
                if new_failures >= 3:
                    await self._store.update_status(
                        task.id,
                        CronTaskStatus.paused,
                        next_fire_at=None,
                        consecutive_failures=new_failures,
                    )
                    return
                natural_next = now + 300  # fallback: try again in 5 min

            backoff_next = now + backoff_delay(new_failures)
            next_at = max(natural_next, backoff_next)
            await self._store.update_status(
                task.id,
                CronTaskStatus.active,
                next_fire_at=next_at,
                consecutive_failures=new_failures,
            )
        else:
            # One-shot: limited transient retries
            if is_transient_error(error) and new_failures <= ONESHOT_MAX_TRANSIENT_RETRIES:
                next_at = now + backoff_delay(new_failures)
                await self._store.update_status(
                    task.id,
                    CronTaskStatus.active,
                    next_fire_at=next_at,
                    consecutive_failures=new_failures,
                )
            else:
                # Permanent error or retries exhausted
                await self._store.update_status(
                    task.id,
                    CronTaskStatus.paused,
                    next_fire_at=None,
                    consecutive_failures=new_failures,
                )

        # Failure alert (OpenClaw CronFailureAlert)
        if (
            task.failure_alert
            and new_failures >= task.failure_alert.after
            and (
                task.last_failure_alert_at is None
                or now - task.last_failure_alert_at >= task.failure_alert.cooldown_seconds
            )
        ):
            if hasattr(self._executor, "_delivery_router") and self._executor._delivery_router:
                try:
                    await self._executor._delivery_router.deliver_alert(task, error)
                    await self._store.update_status(
                        task.id,
                        task.status,  # keep current status
                        last_failure_alert_at=now,
                    )
                except Exception:
                    logger.exception("Failure alert delivery failed for %s", task.id)

    # ------------------------------------------------------------------
    # Multi-instance claim protocol
    # ------------------------------------------------------------------

    async def _claim_tasks(self, tasks: list[CronTask]) -> list[CronTask]:
        """Atomically claim due tasks via SQLite CAS."""
        claimed: list[CronTask] = []
        now = time.time()
        db = self._store.db
        for task in tasks:
            if not task.durable:
                # Non-durable tasks are memory-only, no CAS needed
                # (only visible to this kernel instance)
                task.running_by = self._kernel_id
                task.running_heartbeat = now
                claimed.append(task)
                continue

            cursor = await db.execute(
                """UPDATE cron_tasks
                SET running_by = ?, running_heartbeat = ?
                WHERE id = ? AND running_by IS NULL AND status = 'active'""",
                (self._kernel_id, now, task.id),
            )
            await db.commit()
            if cursor.rowcount > 0:
                task.running_by = self._kernel_id
                task.running_heartbeat = now
                claimed.append(task)

        return claimed

    async def _release_task(self, task_id: str) -> None:
        """Release the claim after execution completes."""
        db = self._store.db
        await db.execute(
            "UPDATE cron_tasks SET running_by = NULL, running_heartbeat = NULL "
            "WHERE id = ? AND running_by = ?",
            (task_id, self._kernel_id),
        )
        await db.commit()

    async def _cleanup_stale_claims(self) -> None:
        """Clear abandoned claims from crashed kernel instances.

        Called every tick (not just startup) so that a crash of another
        kernel running alongside this one is detected within ≤60 s + 2 min.
        """
        db = self._store.db
        cutoff = time.time() - HEARTBEAT_STALE_THRESHOLD_S
        cursor = await db.execute(
            "UPDATE cron_tasks SET running_by = NULL, running_heartbeat = NULL "
            "WHERE running_by IS NOT NULL AND running_heartbeat < ?",
            (cutoff,),
        )
        await db.commit()
        if cursor.rowcount > 0:
            logger.warning(
                "Cleared %d stale cron claims (heartbeat > %.0fs old)",
                cursor.rowcount,
                HEARTBEAT_STALE_THRESHOLD_S,
            )

    # ------------------------------------------------------------------
    # Session reaper
    # ------------------------------------------------------------------

    async def _reap_sessions(self) -> None:
        """Clean up expired cron execution sessions.

        Sessions tagged with ``source="cron"`` older than
        ``session_retention_hours`` are removed from SessionManager.
        Set ``session_retention_hours=0`` to disable.
        """
        if self._session_retention_hours <= 0:
            return
        # Reaping requires SessionManager access, which we get through
        # the executor's session_manager reference.
        if self._session_retention_hours <= 0:
            return  # 0 = disabled

        cutoff = time.time() - self._session_retention_hours * 3600
        session_mgr = self._executor._session_manager

        # Find old cron execution session IDs from the store
        db = self._store.db
        async with db.execute(
            "SELECT DISTINCT session_id FROM cron_executions WHERE started_at < ?",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return

        deleted = 0
        for row in rows:
            sid = row[0]
            try:
                ok = await session_mgr.delete_session(sid)
                if ok:
                    deleted += 1
            except Exception:
                pass  # best-effort

        if deleted:
            logger.info("Session reaper: deleted %d expired cron sessions", deleted)

    # ------------------------------------------------------------------
    # Startup catch-up
    # ------------------------------------------------------------------

    async def handle_startup_catchup(self) -> None:
        """Fire missed tasks from before this kernel started.

        OpenClaw stagger strategy: first MAX_IMMEDIATE_CATCHUP tasks
        fire immediately; the rest are staggered by STARTUP_STAGGER_S.
        """
        await self._cleanup_stale_claims()

        active = await self._store.list_active()
        now = time.time()
        missed = [
            t
            for t in active
            if t.next_fire_at is not None and t.next_fire_at < now and t.running_by is None
        ]

        if not missed:
            return

        logger.info("Startup catch-up: %d missed tasks", len(missed))

        for i, task in enumerate(missed):
            if task.recurring and not task.repeat.is_exhausted(
                task.fire_count, task.created_at, now
            ):
                # Recurring: skip to next future fire instead of replaying
                try:
                    next_at = compute_next_fire(task.schedule, from_time=now)
                    await self._store.update_status(
                        task.id,
                        CronTaskStatus.active,
                        next_fire_at=next_at,
                    )
                except Exception:
                    logger.warning("Cannot compute next fire for %s — skipping", task.id)
                continue

            # One-shot missed: fire now (with stagger for overflow)
            if i >= MAX_IMMEDIATE_CATCHUP:
                await asyncio.sleep(STARTUP_STAGGER_S)

            claimed = await self._claim_tasks([task])
            if claimed:
                await self._fire_task(claimed[0])
