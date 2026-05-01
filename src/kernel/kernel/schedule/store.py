"""CronStore — SQLite persistence for cron tasks and execution records.

Design reference: ``docs/plans/schedule-manager.md`` § 3.3.

Storage lives in ``~/.mustang/state/kernel.db`` (separate from the
session database).  ``durable=False`` tasks are kept in an in-memory
dict only and vanish on kernel restart.
"""

from __future__ import annotations

import orjson
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from kernel.schedule.types import (
    CronExecution,
    CronTask,
    CronTaskStatus,
    DeliveryConfig,
    FailureAlertConfig,
    RepeatConfig,
    Schedule,
    ScheduleKind,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS cron_tasks (
    id                  TEXT PRIMARY KEY,
    schedule_kind       TEXT NOT NULL,
    schedule_expr       TEXT NOT NULL DEFAULT '',
    schedule_interval   REAL NOT NULL DEFAULT 0,
    schedule_run_at     REAL NOT NULL DEFAULT 0,
    prompt              TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    recurring           INTEGER NOT NULL DEFAULT 1,
    durable             INTEGER NOT NULL DEFAULT 1,
    skills              TEXT NOT NULL DEFAULT '[]',
    model               TEXT,
    timeout_seconds     REAL NOT NULL DEFAULT 1800,
    inactivity_timeout  REAL NOT NULL DEFAULT 600,
    delivery_target     TEXT NOT NULL DEFAULT 'session,acp',
    delivery_on_failure INTEGER NOT NULL DEFAULT 1,
    delivery_silent_pattern TEXT NOT NULL DEFAULT '',
    session_id          TEXT,
    project_dir         TEXT,
    created_at          REAL NOT NULL,
    last_fired_at       REAL,
    next_fire_at        REAL,
    status              TEXT NOT NULL DEFAULT 'active',
    fire_count          INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    max_age_seconds     REAL NOT NULL DEFAULT 604800,
    repeat_max_count    INTEGER,
    repeat_max_duration REAL,
    repeat_until        REAL,
    failure_alert_after INTEGER,
    failure_alert_cooldown REAL,
    failure_alert_target TEXT,
    last_failure_alert_at REAL,
    running_by          TEXT,
    running_heartbeat   REAL
);

CREATE TABLE IF NOT EXISTS cron_executions (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES cron_tasks(id),
    session_id      TEXT NOT NULL,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    duration_ms     REAL,
    status          TEXT NOT NULL DEFAULT 'running',
    error           TEXT,
    stop_reason     TEXT,
    summary         TEXT,
    delivery_status TEXT,
    delivery_error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_cron_tasks_next_fire
    ON cron_tasks(next_fire_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_cron_executions_task
    ON cron_executions(task_id);
CREATE INDEX IF NOT EXISTS idx_cron_executions_started
    ON cron_executions(started_at);
"""


class CronStore:
    """SQLite + in-memory persistence for cron tasks.

    Durable tasks live in SQLite (``kernel.db``).  Non-durable tasks
    live in ``_memory`` only and disappear on restart.

    Multi-instance note: non-durable tasks are invisible to other
    kernel instances sharing the same ``kernel.db``.
    """

    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None
        self._memory: dict[str, CronTask] = {}  # non-durable tasks
        self._db_path: Path | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self, db_path: Path) -> None:
        """Open the database and ensure schema exists."""
        self._db_path = db_path
        self._db = await aiosqlite.connect(str(db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.commit()
        logger.info("CronStore opened: %s", db_path)

    async def shutdown(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
        self._memory.clear()

    @property
    def db(self) -> aiosqlite.Connection:
        """Direct access for claim/heartbeat SQL (CronScheduler)."""
        if self._db is None:
            raise RuntimeError("CronStore not started")
        return self._db

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    async def add(self, task: CronTask) -> None:
        """Persist a new task."""
        if not task.durable:
            self._memory[task.id] = task
            return
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO cron_tasks (
                id, schedule_kind, schedule_expr, schedule_interval,
                schedule_run_at, prompt, description, recurring, durable,
                skills, model, timeout_seconds, inactivity_timeout,
                delivery_target, delivery_on_failure, delivery_silent_pattern,
                session_id, project_dir, created_at, last_fired_at,
                next_fire_at, status, fire_count, consecutive_failures,
                max_age_seconds, repeat_max_count, repeat_max_duration,
                repeat_until, failure_alert_after, failure_alert_cooldown,
                failure_alert_target, last_failure_alert_at,
                running_by, running_heartbeat
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?
            )""",
            _task_to_row(task),
        )
        await self._db.commit()

    async def remove(self, task_id: str) -> bool:
        """Soft-delete a task.  Returns False if not found."""
        if task_id in self._memory:
            self._memory[task_id].status = CronTaskStatus.deleted
            del self._memory[task_id]
            return True
        assert self._db is not None
        cursor = await self._db.execute(
            "UPDATE cron_tasks SET status = ? WHERE id = ?",
            (CronTaskStatus.deleted.value, task_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def get(self, task_id: str) -> CronTask | None:
        """Fetch a single task by ID."""
        if task_id in self._memory:
            return self._memory[task_id]
        assert self._db is not None
        async with self._db.execute("SELECT * FROM cron_tasks WHERE id = ?", (task_id,)) as cursor:
            row = await cursor.fetchone()
            return _row_to_task(row) if row else None

    async def list_all(self) -> list[CronTask]:
        """Return all tasks (including completed / deleted)."""
        assert self._db is not None
        async with self._db.execute("SELECT * FROM cron_tasks") as cursor:
            rows = await cursor.fetchall()
        tasks = [_row_to_task(r) for r in rows]
        tasks.extend(self._memory.values())
        return tasks

    async def list_active(self) -> list[CronTask]:
        """Return only ``active`` tasks (durable + non-durable)."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM cron_tasks WHERE status = ?",
            (CronTaskStatus.active.value,),
        ) as cursor:
            rows = await cursor.fetchall()
        tasks = [_row_to_task(r) for r in rows]
        tasks.extend(t for t in self._memory.values() if t.status == CronTaskStatus.active)
        return tasks

    async def update_fired(
        self,
        task_id: str,
        fired_at: float,
        next_at: float | None,
    ) -> None:
        """Record a successful fire: bump fire_count, update timestamps."""
        if task_id in self._memory:
            t = self._memory[task_id]
            t.last_fired_at = fired_at
            t.next_fire_at = next_at
            t.fire_count += 1
            t.consecutive_failures = 0
            return
        assert self._db is not None
        await self._db.execute(
            """UPDATE cron_tasks SET
                last_fired_at = ?, next_fire_at = ?,
                fire_count = fire_count + 1, consecutive_failures = 0
            WHERE id = ?""",
            (fired_at, next_at, task_id),
        )
        await self._db.commit()

    async def update_status(
        self,
        task_id: str,
        status: CronTaskStatus,
        *,
        next_fire_at: float | None = ...,  # type: ignore[assignment]
        consecutive_failures: int | None = None,
        last_failure_alert_at: float | None = ...,  # type: ignore[assignment]
    ) -> None:
        """Update task status and optional fields atomically."""
        if task_id in self._memory:
            t = self._memory[task_id]
            t.status = status
            if next_fire_at is not ...:
                t.next_fire_at = next_fire_at  # type: ignore[assignment]
            if consecutive_failures is not None:
                t.consecutive_failures = consecutive_failures
            if last_failure_alert_at is not ...:
                t.last_failure_alert_at = last_failure_alert_at  # type: ignore[assignment]
            return

        assert self._db is not None
        sets: list[str] = ["status = ?"]
        params: list[Any] = [status.value]
        if next_fire_at is not ...:
            sets.append("next_fire_at = ?")
            params.append(next_fire_at)
        if consecutive_failures is not None:
            sets.append("consecutive_failures = ?")
            params.append(consecutive_failures)
        if last_failure_alert_at is not ...:
            sets.append("last_failure_alert_at = ?")
            params.append(last_failure_alert_at)
        params.append(task_id)
        await self._db.execute(
            f"UPDATE cron_tasks SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Execution records
    # ------------------------------------------------------------------

    async def add_execution(self, execution: CronExecution) -> None:
        """Insert a new execution record."""
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO cron_executions (
                id, task_id, session_id, started_at, ended_at,
                duration_ms, status, error, stop_reason, summary,
                delivery_status, delivery_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                execution.id,
                execution.task_id,
                execution.session_id,
                execution.started_at,
                execution.ended_at,
                execution.duration_ms,
                execution.status,
                execution.error,
                execution.stop_reason,
                execution.summary,
                execution.delivery_status,
                execution.delivery_error,
            ),
        )
        await self._db.commit()

    async def update_execution(
        self,
        execution_id: str,
        *,
        ended_at: float | None = None,
        duration_ms: float | None = None,
        status: str | None = None,
        error: str | None = None,
        stop_reason: str | None = None,
        summary: str | None = None,
        delivery_status: str | None = None,
        delivery_error: str | None = None,
    ) -> None:
        """Update mutable fields of an execution record."""
        assert self._db is not None
        sets: list[str] = []
        params: list[Any] = []
        for col, val in [
            ("ended_at", ended_at),
            ("duration_ms", duration_ms),
            ("status", status),
            ("error", error),
            ("stop_reason", stop_reason),
            ("summary", summary),
            ("delivery_status", delivery_status),
            ("delivery_error", delivery_error),
        ]:
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if not sets:
            return
        params.append(execution_id)
        await self._db.execute(
            f"UPDATE cron_executions SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        await self._db.commit()

    async def list_executions(self, task_id: str, limit: int = 20) -> list[CronExecution]:
        """Return recent executions for a task, newest first."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM cron_executions WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_execution(r) for r in rows]

    async def prune_executions(self, retention_days: int = 30) -> int:
        """Delete execution records older than *retention_days*.

        Returns the number of rows deleted.
        """
        assert self._db is not None
        cutoff = time.time() - retention_days * 86400
        cursor = await self._db.execute(
            "DELETE FROM cron_executions WHERE started_at < ?", (cutoff,)
        )
        await self._db.commit()
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Row ↔ CronTask mapping
# ---------------------------------------------------------------------------


def _task_to_row(t: CronTask) -> tuple[Any, ...]:
    """Flatten a CronTask to a positional row for INSERT."""
    fa = t.failure_alert
    return (
        t.id,
        t.schedule.kind.value,
        t.schedule.expr,
        t.schedule.interval_seconds,
        t.schedule.run_at,
        t.prompt,
        t.description,
        int(t.recurring),
        int(t.durable),
        orjson.dumps(t.skills).decode(),
        t.model,
        t.timeout_seconds,
        t.inactivity_timeout_seconds,
        t.delivery.target,
        int(t.delivery.on_failure),
        t.delivery.silent_pattern,
        t.session_id,
        t.project_dir,
        t.created_at,
        t.last_fired_at,
        t.next_fire_at,
        t.status.value,
        t.fire_count,
        t.consecutive_failures,
        t.max_age_seconds,
        t.repeat.max_count,
        t.repeat.max_duration_seconds,
        t.repeat.until,
        fa.after if fa else None,
        fa.cooldown_seconds if fa else None,
        fa.target if fa else None,
        t.last_failure_alert_at,
        t.running_by,
        t.running_heartbeat,
    )


def _row_to_task(row: aiosqlite.Row) -> CronTask:
    """Reconstruct a CronTask from a database row."""
    fa_after = row["failure_alert_after"]
    failure_alert = (
        FailureAlertConfig(
            after=fa_after,
            cooldown_seconds=row["failure_alert_cooldown"] or 3600,
            target=row["failure_alert_target"] or "session",
        )
        if fa_after is not None
        else None
    )
    return CronTask(
        id=row["id"],
        schedule=Schedule(
            kind=ScheduleKind(row["schedule_kind"]),
            expr=row["schedule_expr"],
            interval_seconds=row["schedule_interval"],
            run_at=row["schedule_run_at"],
        ),
        prompt=row["prompt"],
        description=row["description"],
        recurring=bool(row["recurring"]),
        durable=bool(row["durable"]),
        skills=orjson.loads(row["skills"]),
        model=row["model"],
        timeout_seconds=row["timeout_seconds"],
        inactivity_timeout_seconds=row["inactivity_timeout"],
        delivery=DeliveryConfig(
            target=row["delivery_target"],
            on_failure=bool(row["delivery_on_failure"]),
            silent_pattern=row["delivery_silent_pattern"],
        ),
        session_id=row["session_id"],
        project_dir=row["project_dir"],
        created_at=row["created_at"],
        last_fired_at=row["last_fired_at"],
        next_fire_at=row["next_fire_at"],
        status=CronTaskStatus(row["status"]),
        fire_count=row["fire_count"],
        consecutive_failures=row["consecutive_failures"],
        max_age_seconds=row["max_age_seconds"],
        repeat=RepeatConfig(
            max_count=row["repeat_max_count"],
            max_duration_seconds=row["repeat_max_duration"],
            until=row["repeat_until"],
        ),
        failure_alert=failure_alert,
        last_failure_alert_at=row["last_failure_alert_at"],
        running_by=row["running_by"],
        running_heartbeat=row["running_heartbeat"],
    )


def _row_to_execution(row: aiosqlite.Row) -> CronExecution:
    """Reconstruct a CronExecution from a database row."""
    return CronExecution(
        id=row["id"],
        task_id=row["task_id"],
        session_id=row["session_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        duration_ms=row["duration_ms"],
        status=row["status"],
        error=row["error"],
        stop_reason=row["stop_reason"],
        summary=row["summary"],
        delivery_status=row["delivery_status"],
        delivery_error=row["delivery_error"],
    )
