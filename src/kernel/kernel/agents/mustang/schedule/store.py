"""ResourceStore-backed persistence for scheduled tasks."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

import orjson
import sqlalchemy as sa
import yaml

from kernel.agents.mustang.schedule.types import (
    CronExecution,
    CronTask,
    CronTaskStatus,
    DeliveryConfig,
    FailureAlertConfig,
    RepeatConfig,
    Schedule,
    ScheduleKind,
)
from kernel.core.storage import ResourceStore, tables

logger = logging.getLogger(__name__)


class CronStore:
    """ResourceStore + in-memory persistence for cron tasks.

    Durable scheduled tasks live in ResourceStore ``scheduled_tasks`` rows.
    Non-durable tasks and execution records remain process-local because they
    are runtime state, not global user truth.
    """

    def __init__(self) -> None:
        self._store: ResourceStore | None = None
        self._memory: dict[str, CronTask] = {}
        self._executions: dict[str, CronExecution] = {}
        self._home: Path | None = None
        self.legacy_import_warnings: list[str] = []

    async def startup(self, db_path: Path) -> None:
        """Open ResourceStore, accepting old tests that pass a DB path."""
        home = db_path.parent if db_path.suffix == ".db" else db_path
        await self.startup_resource(home)

    async def startup_resource(self, home: Path) -> None:
        """Open ResourceStore and import legacy schedules once if present."""
        self._home = home
        self._store = ResourceStore.open(home)
        self._import_legacy_yaml_once()
        logger.info("CronStore opened ResourceStore home: %s", home)

    async def shutdown(self) -> None:
        """Close ResourceStore and clear process-local state."""
        if self._store is not None:
            self._store.close()
            self._store = None
        self._memory.clear()
        self._executions.clear()

    async def add(self, task: CronTask) -> None:
        """Persist a new task."""
        if not task.durable:
            self._memory[task.id] = task
            return
        now = _now_iso()

        def _write(conn: Any) -> None:
            existing = conn.execute(
                sa.select(tables.scheduled_tasks.c.task_id).where(
                    tables.scheduled_tasks.c.task_id == task.id
                )
            ).fetchone()
            if existing is not None:
                raise ValueError(f"scheduled task already exists: {task.id}")
            conn.execute(
                tables.scheduled_tasks.insert().values(
                    task_id=task.id,
                    owner_agent_id=task.owner_agent_id,
                    title=task.description or task.prompt[:80],
                    schedule_json=_json(_schedule_payload(task)),
                    target_json=_json(_target_payload(task)),
                    status=task.status.value,
                    revision=1,
                    created_at=str(task.created_at),
                    updated_at=now,
                    updated_by_agent_id=task.owner_agent_id,
                )
            )
            _insert_event(conn, task, "schedule.created", revision=1)

        self._require_store().write_tx(_write)

    async def remove(self, task_id: str) -> bool:
        """Soft-delete a task. Returns False if not found."""
        if task_id in self._memory:
            self._memory[task_id].status = CronTaskStatus.deleted
            del self._memory[task_id]
            return True
        return self._update_row(task_id, status=CronTaskStatus.deleted.value, next_fire_at=None)

    async def get(self, task_id: str) -> CronTask | None:
        """Fetch a single task by ID."""
        if task_id in self._memory:
            return self._memory[task_id]
        row = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(tables.scheduled_tasks).where(tables.scheduled_tasks.c.task_id == task_id)
            ).fetchone()
        )
        return _row_to_task(row) if row is not None else None

    async def list_all(self) -> list[CronTask]:
        """Return all tasks, including completed/deleted."""
        rows = self._require_store().read_tx(
            lambda conn: conn.execute(sa.select(tables.scheduled_tasks)).fetchall()
        )
        tasks = [_row_to_task(row) for row in rows]
        tasks.extend(self._memory.values())
        return tasks

    async def list_active(self) -> list[CronTask]:
        """Return active tasks."""
        rows = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(tables.scheduled_tasks).where(
                    tables.scheduled_tasks.c.status == CronTaskStatus.active.value
                )
            ).fetchall()
        )
        tasks = [_row_to_task(row) for row in rows]
        tasks.extend(t for t in self._memory.values() if t.status == CronTaskStatus.active)
        return tasks

    async def update_fired(
        self,
        task_id: str,
        fired_at: float,
        next_at: float | None,
    ) -> None:
        """Record a successful fire."""
        if task_id in self._memory:
            task = self._memory[task_id]
            task.last_fired_at = fired_at
            task.next_fire_at = next_at
            task.fire_count += 1
            task.consecutive_failures = 0
            return
        loaded = await self.get(task_id)
        if loaded is None:
            return
        loaded.last_fired_at = fired_at
        loaded.next_fire_at = next_at
        loaded.fire_count += 1
        loaded.consecutive_failures = 0
        self._replace_task(loaded, event_type="schedule.fired")

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
            task = self._memory[task_id]
            task.status = status
            if next_fire_at is not ...:
                task.next_fire_at = next_fire_at  # type: ignore[assignment]
            if consecutive_failures is not None:
                task.consecutive_failures = consecutive_failures
            if last_failure_alert_at is not ...:
                task.last_failure_alert_at = last_failure_alert_at  # type: ignore[assignment]
            return
        loaded = await self.get(task_id)
        if loaded is None:
            return
        loaded.status = status
        if next_fire_at is not ...:
            loaded.next_fire_at = next_fire_at  # type: ignore[assignment]
        if consecutive_failures is not None:
            loaded.consecutive_failures = consecutive_failures
        if last_failure_alert_at is not ...:
            loaded.last_failure_alert_at = last_failure_alert_at  # type: ignore[assignment]
        self._replace_task(loaded, event_type="schedule.updated")

    async def claim_tasks(self, tasks: list[CronTask], kernel_id: str) -> list[CronTask]:
        """Claim active due tasks by CAS on ``running_by``."""
        claimed: list[CronTask] = []
        now = time.time()
        for task in tasks:
            if not task.durable:
                task.running_by = kernel_id
                task.running_heartbeat = now
                claimed.append(task)
                continue

            def _write(conn: Any) -> int:
                current = conn.execute(
                    sa.select(tables.scheduled_tasks).where(
                        tables.scheduled_tasks.c.task_id == task.id,
                        tables.scheduled_tasks.c.status == CronTaskStatus.active.value,
                    )
                ).fetchone()
                if current is None:
                    return 0
                loaded = _row_to_task(current)
                if loaded.running_by is not None:
                    return 0
                loaded.running_by = kernel_id
                loaded.running_heartbeat = now
                return _write_task_update(conn, loaded, event_type="schedule.claimed")

            if self._require_store().write_tx(_write) > 0:
                task.running_by = kernel_id
                task.running_heartbeat = now
                claimed.append(task)
        return claimed

    async def release_task(self, task_id: str, kernel_id: str) -> None:
        """Release a claim after execution completes."""
        task = await self.get(task_id)
        if task is None or task.running_by != kernel_id:
            return
        task.running_by = None
        task.running_heartbeat = None
        self._replace_task(task, event_type="schedule.released")

    async def heartbeat(self, task_id: str, kernel_id: str) -> None:
        """Refresh the claim heartbeat for one task."""
        task = await self.get(task_id)
        if task is None or task.running_by != kernel_id:
            return
        task.running_heartbeat = time.time()
        self._replace_task(task, event_type="schedule.heartbeat")

    async def cleanup_stale_claims(self, cutoff: float) -> int:
        """Clear claims whose heartbeat is older than cutoff."""
        active = await self.list_active()
        cleared = 0
        for task in active:
            if task.running_by is not None and (task.running_heartbeat or 0) < cutoff:
                task.running_by = None
                task.running_heartbeat = None
                self._replace_task(task, event_type="schedule.claim_cleared")
                cleared += 1
        return cleared

    async def disable_tasks_for_agent(
        self,
        agent_id: str,
        *,
        actor_agent_id: str,
        reason: str = "agent_deleted",
    ) -> int:
        """Pause active schedules owned by a deleted Agent."""
        del reason
        tasks = [
            task
            for task in await self.list_all()
            if task.owner_agent_id == agent_id and task.status == CronTaskStatus.active
        ]
        for task in tasks:
            task.status = CronTaskStatus.paused
            task.next_fire_at = None
            self._replace_task(
                task,
                event_type="schedule.disabled_for_agent",
                actor_agent_id=actor_agent_id,
            )
        return len(tasks)

    def current_revision(self, task_id: str) -> int | None:
        """Return the current ResourceStore revision for one scheduled task."""
        row = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(tables.scheduled_tasks.c.revision).where(
                    tables.scheduled_tasks.c.task_id == task_id
                )
            ).fetchone()
        )
        return int(row["revision"]) if row is not None else None

    async def add_execution(self, execution: CronExecution) -> None:
        """Insert a process-local execution record."""
        self._executions[execution.id] = execution

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
        """Update process-local execution fields."""
        execution = self._executions.get(execution_id)
        if execution is None:
            return
        for key, value in {
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "status": status,
            "error": error,
            "stop_reason": stop_reason,
            "summary": summary,
            "delivery_status": delivery_status,
            "delivery_error": delivery_error,
        }.items():
            if value is not None:
                setattr(execution, key, value)

    async def list_executions(self, task_id: str, limit: int = 20) -> list[CronExecution]:
        """Return recent process-local executions for a task."""
        rows = [
            execution for execution in self._executions.values() if execution.task_id == task_id
        ]
        return sorted(rows, key=lambda execution: execution.started_at, reverse=True)[:limit]

    async def prune_executions(self, retention_days: int = 30) -> int:
        """Delete process-local execution records older than retention."""
        cutoff = time.time() - retention_days * 86400
        old = [
            execution_id
            for execution_id, execution in self._executions.items()
            if execution.started_at < cutoff
        ]
        for execution_id in old:
            self._executions.pop(execution_id, None)
        return len(old)

    async def old_execution_session_ids(self, cutoff: float) -> list[str]:
        """Return unique session ids from old process-local executions."""
        return sorted(
            {
                execution.session_id
                for execution in self._executions.values()
                if execution.started_at < cutoff
            }
        )

    def _replace_task(
        self,
        task: CronTask,
        *,
        event_type: str,
        actor_agent_id: str | None = None,
    ) -> None:
        self._require_store().write_tx(
            lambda conn: _write_task_update(conn, task, event_type, actor_agent_id=actor_agent_id)
        )

    def _update_row(self, task_id: str, **values: Any) -> bool:
        task = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(tables.scheduled_tasks).where(tables.scheduled_tasks.c.task_id == task_id)
            ).fetchone()
        )
        if task is None:
            return False
        loaded = _row_to_task(task)
        if "status" in values:
            loaded.status = CronTaskStatus(values["status"])
        if "next_fire_at" in values:
            loaded.next_fire_at = values["next_fire_at"]
        self._replace_task(loaded, event_type="schedule.deleted")
        return True

    def _import_legacy_yaml_once(self) -> None:
        if self._home is None:
            return
        path = self._home / "config" / "schedules.yaml"
        if not path.exists():
            return
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        marker_key = "legacy.schedule_yaml"
        marker = self._require_store().get_resource(marker_key)
        if marker is not None:
            marker_payload = orjson.loads(marker.payload_json)
            if marker_payload.get("source_hash") != source_hash:
                self.legacy_import_warnings.append(
                    "legacy schedules.yaml drift ignored; ResourceStore is durable truth"
                )
            return
        existing = self._require_store().read_tx(
            lambda conn: conn.execute(
                sa.select(sa.func.count()).select_from(tables.scheduled_tasks)
            ).fetchone()[0]
        )
        if existing:
            self._require_store().cas_put_resource(
                marker_key,
                _json({"source_hash": source_hash, "imported": False, "reason": "db_not_empty"}),
                actor="system",
            )
            return
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tasks = raw.get("tasks") if isinstance(raw, dict) else None
        if not isinstance(tasks, list):
            tasks = []
        for item in tasks:
            if not isinstance(item, dict):
                continue
            task = _task_from_legacy(item)

            def _write_legacy(conn: Any, legacy_task: CronTask = task) -> None:
                conn.execute(
                    tables.scheduled_tasks.insert().values(
                        task_id=legacy_task.id,
                        owner_agent_id=legacy_task.owner_agent_id,
                        title=legacy_task.description or legacy_task.prompt[:80],
                        schedule_json=_json(_schedule_payload(legacy_task)),
                        target_json=_json(_target_payload(legacy_task)),
                        status=legacy_task.status.value,
                        revision=1,
                        created_at=str(legacy_task.created_at),
                        updated_at=_now_iso(),
                        updated_by_agent_id="legacy-import",
                    )
                )
                _insert_event(conn, legacy_task, "schedule.legacy_imported", revision=1)

            self._require_store().write_tx(_write_legacy)
        self._require_store().cas_put_resource(
            marker_key,
            _json({"source_hash": source_hash, "imported": True, "count": len(tasks)}),
            actor="system",
        )

    def _require_store(self) -> ResourceStore:
        if self._store is None:
            raise RuntimeError("CronStore not started")
        return self._store


def _write_task_update(
    conn: Any,
    task: CronTask,
    event_type: str,
    *,
    actor_agent_id: str | None = None,
) -> int:
    current = conn.execute(
        sa.select(tables.scheduled_tasks.c.revision).where(
            tables.scheduled_tasks.c.task_id == task.id
        )
    ).fetchone()
    if current is None:
        return 0
    revision = int(current["revision"]) + 1
    conn.execute(
        tables.scheduled_tasks.update()
        .where(tables.scheduled_tasks.c.task_id == task.id)
        .values(
            owner_agent_id=task.owner_agent_id,
            title=task.description or task.prompt[:80],
            schedule_json=_json(_schedule_payload(task)),
            target_json=_json(_target_payload(task)),
            status=task.status.value,
            revision=revision,
            updated_at=_now_iso(),
            updated_by_agent_id=actor_agent_id or task.owner_agent_id,
        )
    )
    _insert_event(conn, task, event_type, revision=revision, actor_agent_id=actor_agent_id)
    return 1


def _insert_event(
    conn: Any,
    task: CronTask,
    event_type: str,
    *,
    revision: int,
    actor_agent_id: str | None = None,
) -> None:
    payload = _json({"status": task.status.value, "event_type": event_type})
    conn.execute(
        tables.scheduled_task_events.insert().values(
            task_id=task.id,
            event_type=event_type,
            revision=revision,
            actor_agent_id=actor_agent_id or task.owner_agent_id,
            owner_agent_id=task.owner_agent_id,
            created_at=_now_iso(),
            payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
        )
    )


def _task_from_legacy(item: dict[str, Any]) -> CronTask:
    schedule_data = item.get("schedule")
    if not isinstance(schedule_data, dict):
        schedule_data = {"kind": "every", "interval_seconds": item.get("interval_seconds", 60)}
    return CronTask(
        id=str(item.get("id") or item.get("task_id")),
        owner_agent_id=str(item.get("owner_agent_id") or "primary"),
        schedule=Schedule(
            kind=ScheduleKind(str(schedule_data.get("kind", "every"))),
            expr=str(schedule_data.get("expr") or ""),
            interval_seconds=float(schedule_data.get("interval_seconds") or 0),
            run_at=float(schedule_data.get("run_at") or 0),
        ),
        prompt=str(item.get("prompt") or ""),
        description=str(item.get("description") or ""),
        recurring=bool(item.get("recurring", True)),
        durable=True,
        session_id=item.get("session_id"),
        project_dir=item.get("project_dir"),
        created_at=float(item.get("created_at") or time.time()),
        next_fire_at=item.get("next_fire_at"),
        status=CronTaskStatus(str(item.get("status") or CronTaskStatus.active.value)),
    )


def _schedule_payload(task: CronTask) -> dict[str, Any]:
    failure_alert = task.failure_alert
    return {
        "kind": task.schedule.kind.value,
        "expr": task.schedule.expr,
        "interval_seconds": task.schedule.interval_seconds,
        "run_at": task.schedule.run_at,
        "recurring": task.recurring,
        "last_fired_at": task.last_fired_at,
        "next_fire_at": task.next_fire_at,
        "fire_count": task.fire_count,
        "consecutive_failures": task.consecutive_failures,
        "repeat": {
            "max_count": task.repeat.max_count,
            "max_duration_seconds": task.repeat.max_duration_seconds,
            "until": task.repeat.until,
        },
        "failure_alert": (
            {
                "after": failure_alert.after,
                "cooldown_seconds": failure_alert.cooldown_seconds,
                "target": failure_alert.target,
            }
            if failure_alert
            else None
        ),
        "last_failure_alert_at": task.last_failure_alert_at,
        "running_by": task.running_by,
        "running_heartbeat": task.running_heartbeat,
    }


def _target_payload(task: CronTask) -> dict[str, Any]:
    return {
        "prompt": task.prompt,
        "description": task.description,
        "durable": task.durable,
        "skills": task.skills,
        "model": task.model,
        "timeout_seconds": task.timeout_seconds,
        "inactivity_timeout_seconds": task.inactivity_timeout_seconds,
        "delivery": {
            "target": task.delivery.target,
            "on_failure": task.delivery.on_failure,
            "silent_pattern": task.delivery.silent_pattern,
        },
        "session_id": task.session_id,
        "project_dir": task.project_dir,
        "max_age_seconds": task.max_age_seconds,
    }


def _row_to_task(row: Any) -> CronTask:
    schedule_data = orjson.loads(row["schedule_json"])
    target_data = orjson.loads(row["target_json"])
    repeat_data = schedule_data.get("repeat") or {}
    failure_alert_data = schedule_data.get("failure_alert")
    delivery_data = target_data.get("delivery") or {}
    return CronTask(
        id=str(row["task_id"]),
        owner_agent_id=str(row["owner_agent_id"]),
        schedule=Schedule(
            kind=ScheduleKind(schedule_data["kind"]),
            expr=str(schedule_data.get("expr") or ""),
            interval_seconds=float(schedule_data.get("interval_seconds") or 0),
            run_at=float(schedule_data.get("run_at") or 0),
        ),
        prompt=str(target_data.get("prompt") or ""),
        description=str(target_data.get("description") or ""),
        recurring=bool(schedule_data.get("recurring", True)),
        durable=bool(target_data.get("durable", True)),
        skills=list(target_data.get("skills") or []),
        model=target_data.get("model"),
        timeout_seconds=float(target_data.get("timeout_seconds") or 1800),
        inactivity_timeout_seconds=float(target_data.get("inactivity_timeout_seconds") or 600),
        delivery=DeliveryConfig(
            target=str(delivery_data.get("target") or "session,acp"),
            on_failure=bool(delivery_data.get("on_failure", True)),
            silent_pattern=str(delivery_data.get("silent_pattern") or ""),
        ),
        session_id=target_data.get("session_id"),
        project_dir=target_data.get("project_dir"),
        created_at=float(row["created_at"]),
        last_fired_at=schedule_data.get("last_fired_at"),
        next_fire_at=schedule_data.get("next_fire_at"),
        status=CronTaskStatus(str(row["status"])),
        fire_count=int(schedule_data.get("fire_count") or 0),
        consecutive_failures=int(schedule_data.get("consecutive_failures") or 0),
        repeat=RepeatConfig(
            max_count=repeat_data.get("max_count"),
            max_duration_seconds=repeat_data.get("max_duration_seconds"),
            until=repeat_data.get("until"),
        ),
        max_age_seconds=float(target_data.get("max_age_seconds") or 604800),
        failure_alert=(
            FailureAlertConfig(
                after=int(failure_alert_data.get("after") or 3),
                cooldown_seconds=float(failure_alert_data.get("cooldown_seconds") or 3600),
                target=str(failure_alert_data.get("target") or "session"),
            )
            if isinstance(failure_alert_data, dict)
            else None
        ),
        last_failure_alert_at=schedule_data.get("last_failure_alert_at"),
        running_by=schedule_data.get("running_by"),
        running_heartbeat=schedule_data.get("running_heartbeat"),
    )


def _json(payload: object) -> str:
    return orjson.dumps(payload, option=orjson.OPT_SORT_KEYS).decode()


def _now_iso() -> str:
    return str(time.time())
