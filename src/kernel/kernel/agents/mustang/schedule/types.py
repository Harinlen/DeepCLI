"""Data models for the ScheduleManager subsystem.

Design reference: ``docs/plans/schedule-manager.md`` § 2.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CronTaskStatus(str, enum.Enum):
    """Cron task lifecycle states."""

    active = "active"
    paused = "paused"  # manual pause or auto-pause on repeated failure
    expired = "expired"  # max_age exceeded with no activity
    completed = "completed"  # one-shot finished or repeat limit reached
    deleted = "deleted"  # soft-deleted for audit trail


class ScheduleKind(str, enum.Enum):
    """Schedule format — OpenClaw 3 kinds + Hermes duration = 4.

    ``delay`` is a parsing-layer sugar: the parser converts it to ``at``
    (``run_at = now + delay_seconds``) before storage.  SQLite only ever
    stores ``cron``, ``every``, or ``at``.
    """

    cron = "cron"  # 5-field cron expression (local time)
    every = "every"  # fixed interval in seconds
    at = "at"  # absolute epoch timestamp
    delay = "delay"  # relative delay — converted to ``at`` before persist


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class Schedule:
    """Schedule definition.

    Four input formats share one dataclass; ``kind`` determines which
    field is active:

    - ``cron`` — ``expr`` is a 5-field cron string
    - ``every`` — ``interval_seconds`` is the gap between fires
    - ``at`` — ``run_at`` is an absolute epoch-seconds timestamp
    - ``delay`` — parsing sugar; the parser sets ``run_at = now + N``
      then flips ``kind`` to ``at`` before the task reaches CronStore.
    """

    kind: ScheduleKind
    expr: str = ""  # cron only
    interval_seconds: float = 0  # every only
    run_at: float = 0  # at only (delay is converted here)


@dataclass
class RepeatConfig:
    """When to stop repeating a recurring task.

    Three dimensions, any-first-wins:

    - ``max_count`` — fire at most *N* times
    - ``max_duration_seconds`` — from ``created_at``, keep going this long
    - ``until`` — absolute epoch-seconds deadline

    All ``None`` → unlimited (until manual stop or ``max_age`` safety net).
    """

    max_count: int | None = None
    max_duration_seconds: float | None = None
    until: float | None = None

    def is_exhausted(
        self,
        fire_count: int,
        created_at: float,
        now: float | None = None,
    ) -> bool:
        """Return ``True`` when any limit has been reached."""
        if now is None:
            now = time.time()
        if self.max_count is not None and fire_count >= self.max_count:
            return True
        if self.max_duration_seconds is not None and now - created_at >= self.max_duration_seconds:
            return True
        if self.until is not None and now >= self.until:
            return True
        return False


@dataclass
class DeliveryConfig:
    """Where to deliver cron execution results.

    ``target`` is a comma-separated string supporting multiple
    destinations:

    - ``"session"`` — system-reminder injection into the creator session
    - ``"acp"`` — ACP WebSocket broadcast (CronCompletionNotification)
    - ``"gateway:<adapter>:<channel>"`` — GatewayManager announce
    - ``"none"`` — skip delivery (result only in execution record)
    """

    target: str = "session,acp"
    on_failure: bool = True  # deliver error summaries too
    silent_pattern: str = ""  # regex — skip delivery if response matches


@dataclass
class FailureAlertConfig:
    """Per-task failure notification (OpenClaw CronFailureAlert).

    When ``consecutive_failures >= after`` and the cooldown has elapsed,
    a notification is dispatched via DeliveryRouter.
    """

    after: int = 3
    cooldown_seconds: float = 3600  # 1 hour default
    target: str = "session"


# ---------------------------------------------------------------------------
# CronTask — the primary aggregate
# ---------------------------------------------------------------------------


@dataclass
class CronTask:
    """One scheduled cron job.

    Design reference: ``docs/plans/schedule-manager.md`` § 2.1.
    """

    id: str  # 8-char UUID slice
    schedule: Schedule
    prompt: str
    description: str = ""

    # ── behaviour ──
    recurring: bool = True
    # Default is auto-inferred by schedule_parser:
    #   cron/every → True, at/delay → False
    # Validation: at + recurring=True → rejected at creation time.
    durable: bool = True  # False → memory-only, lost on restart

    # ── execution config (Hermes-inspired) ──
    skills: list[str] = field(default_factory=list)
    model: str | None = None  # per-job model override
    timeout_seconds: float = 30 * 60  # 30 min total timeout
    inactivity_timeout_seconds: float = 10 * 60  # 0 = disabled

    # ── delivery (OpenClaw + Hermes) ──
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)

    # ── ownership ──
    session_id: str | None = None  # creator session
    project_dir: str | None = None  # cwd for isolated session

    # ── timestamps (epoch seconds) ──
    created_at: float = 0.0
    last_fired_at: float | None = None
    next_fire_at: float | None = None

    # ── runtime state ──
    status: CronTaskStatus = CronTaskStatus.active
    fire_count: int = 0
    consecutive_failures: int = 0

    # ── repeat limits ──
    repeat: RepeatConfig = field(default_factory=RepeatConfig)
    max_age_seconds: float = 7 * 24 * 3600  # safety net: 0 = disabled
    # max_age_seconds — system safety net, measured from last_fired_at
    #   (no-activity expiry for zombie jobs)
    # RepeatConfig.max_duration_seconds — user-set, from created_at
    #   (unconditional deadline regardless of activity)

    # ── failure alert (OpenClaw) ──
    failure_alert: FailureAlertConfig | None = None
    last_failure_alert_at: float | None = None

    # ── multi-instance coordination ──
    running_by: str | None = None  # kernel instance UUID
    running_heartbeat: float | None = None  # refreshed every 30 s


# ---------------------------------------------------------------------------
# CronExecution — one execution record
# ---------------------------------------------------------------------------


@dataclass
class CronExecution:
    """Record of a single cron task execution.

    Design reference: ``docs/plans/schedule-manager.md`` § 2.2.
    """

    id: str
    task_id: str
    session_id: str
    started_at: float = 0.0
    ended_at: float | None = None
    duration_ms: float | None = None
    status: str = "running"  # running / completed / failed / timeout
    error: str | None = None
    stop_reason: str | None = None
    summary: str | None = None
    delivery_status: str | None = None
    delivery_error: str | None = None
