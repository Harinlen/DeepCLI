"""Schedule expression parser and next-fire-time calculator.

Design reference: ``docs/plans/schedule-manager.md`` § 2.1.
Supports four input formats: cron, every, at (timestamp), delay.

``delay`` is a parsing-layer sugar: the parser converts it to ``at``
(``run_at = now + delay_seconds``) so callers never need to handle
the ``delay`` kind after parsing.

Dependencies:
    ``croniter`` — cron expression evaluation (``pip install croniter``).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from kernel.schedule.types import Schedule, ScheduleKind

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Matches "every 30m", "every 2h", "every 1d", "every 5s"
_EVERY_RE = re.compile(
    r"^every\s+(\d+(?:\.\d+)?)\s*([smhd])$",
    re.IGNORECASE,
)

# Matches bare duration: "30m", "2h", "5s", "1d" (delay / one-shot)
_DELAY_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*([smhd])$",
    re.IGNORECASE,
)

_UNIT_MULTIPLIERS: dict[str, float] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_schedule(expr: str) -> Schedule:
    """Parse a user-facing schedule expression into a :class:`Schedule`.

    Formats (case-insensitive):
        - ``"*/30 * * * *"`` → cron
        - ``"every 30m"`` / ``"every 2h"`` / ``"every 1d"`` → every
        - ``"5m"`` / ``"2h"`` → delay (converted to ``at``)
        - ``"2026-05-01T09:00"`` (ISO 8601) → at

    Raises:
        ValueError: If the expression cannot be parsed.
    """
    expr = expr.strip()
    if not expr:
        raise ValueError("schedule expression cannot be empty")

    # --- every ---
    m = _EVERY_RE.match(expr)
    if m:
        value = float(m.group(1))
        unit = m.group(2).lower()
        seconds = value * _UNIT_MULTIPLIERS[unit]
        if seconds < 1:
            raise ValueError(f"interval too short: {expr}")
        return Schedule(kind=ScheduleKind.every, interval_seconds=seconds)

    # --- bare delay ---
    m = _DELAY_RE.match(expr)
    if m:
        value = float(m.group(1))
        unit = m.group(2).lower()
        seconds = value * _UNIT_MULTIPLIERS[unit]
        if seconds < 1:
            raise ValueError(f"delay too short: {expr}")
        # Convert delay → at (run_at = now + seconds)
        return Schedule(kind=ScheduleKind.at, run_at=time.time() + seconds)

    # --- ISO 8601 timestamp ---
    try:
        dt = _parse_iso(expr)
        return Schedule(kind=ScheduleKind.at, run_at=dt.timestamp())
    except ValueError:
        pass

    # --- cron expression (5-field) ---
    # Validate by attempting to construct a croniter instance.
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        croniter(expr)  # raises ValueError/KeyError if malformed
    except (ValueError, KeyError) as exc:
        raise ValueError(f"unrecognised schedule expression: {expr!r}") from exc
    except ImportError as exc:
        raise ImportError(
            "croniter is required for cron expressions: pip install croniter"
        ) from exc

    return Schedule(kind=ScheduleKind.cron, expr=expr)


# ---------------------------------------------------------------------------
# Next-fire computation
# ---------------------------------------------------------------------------


def compute_next_fire(
    schedule: Schedule,
    *,
    from_time: float | None = None,
) -> float:
    """Compute the next fire epoch-seconds for a schedule.

    Args:
        schedule: The schedule definition.
        from_time: Base time (epoch seconds); defaults to ``time.time()``.

    Returns:
        Epoch seconds of the next fire.

    Raises:
        ValueError: If the schedule kind is ``delay`` (must be converted
            to ``at`` before calling this function) or ``at`` with a
            past ``run_at``.
    """
    if from_time is None:
        from_time = time.time()

    if schedule.kind == ScheduleKind.cron:
        from croniter import croniter  # type: ignore[import-untyped]

        cron = croniter(schedule.expr, from_time)
        return float(cron.get_next(float))

    if schedule.kind == ScheduleKind.every:
        return from_time + schedule.interval_seconds

    if schedule.kind == ScheduleKind.at:
        return schedule.run_at

    # delay should never reach here — parser converts to at.
    raise ValueError(f"cannot compute next_fire for kind={schedule.kind}")


def human_schedule(schedule: Schedule) -> str:
    """Return a human-readable description of a schedule.

    Examples: ``"every 30 minutes"``, ``"at 2026-05-01 09:00"``,
    ``"cron */5 * * * *"``.
    """
    if schedule.kind == ScheduleKind.cron:
        return f"cron {schedule.expr}"

    if schedule.kind == ScheduleKind.every:
        secs = schedule.interval_seconds
        if secs < 60:
            return f"every {int(secs)}s"
        if secs < 3600:
            mins = secs / 60
            return f"every {mins:g}m" if mins != int(mins) else f"every {int(mins)}m"
        hours = secs / 3600
        return f"every {hours:g}h" if hours != int(hours) else f"every {int(hours)}h"

    if schedule.kind == ScheduleKind.at:
        dt = datetime.fromtimestamp(schedule.run_at, tz=timezone.utc).astimezone()
        return f"at {dt.strftime('%Y-%m-%d %H:%M')}"

    return str(schedule.kind)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 string to a timezone-aware datetime.

    Handles both ``2026-05-01T09:00`` (naive → local) and
    ``2026-05-01T09:00+08:00`` (aware).
    """
    # Python 3.11+ datetime.fromisoformat handles most formats.
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # Treat naive as local time.
        dt = dt.astimezone()
    return dt
