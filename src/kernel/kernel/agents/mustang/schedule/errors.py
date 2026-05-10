"""Error classification and backoff for cron task failures.

Design reference: ``docs/plans/schedule-manager.md`` § 3.4.
Ported from OpenClaw ``service/timer.ts`` transient/permanent
classification and exponential backoff schedule.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Transient error patterns (retriable)
# ---------------------------------------------------------------------------
# Network hiccups, provider overload, rate limits — temporary by nature.

TRANSIENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "rate_limit": re.compile(
        r"(rate[_ ]limit|too many requests|429|resource has been exhausted)",
        re.IGNORECASE,
    ),
    "overloaded": re.compile(
        r"\b529\b|overloaded|high demand|capacity exceeded",
        re.IGNORECASE,
    ),
    "network": re.compile(
        r"(network|econnreset|econnrefused|fetch failed|socket)",
        re.IGNORECASE,
    ),
    "timeout": re.compile(r"(timeout|etimedout)", re.IGNORECASE),
    "server_error": re.compile(r"\b5\d{2}\b"),
}

# ---------------------------------------------------------------------------
# Permanent error patterns (not retriable)
# ---------------------------------------------------------------------------
# Configuration errors, missing channels, revoked permissions.

PERMANENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"unsupported channel", re.IGNORECASE),
    re.compile(r"chat not found", re.IGNORECASE),
    re.compile(r"bot.*not.*member", re.IGNORECASE),
    re.compile(r"forbidden", re.IGNORECASE),
    re.compile(r"invalid.*api[_ ]key", re.IGNORECASE),
    re.compile(r"authentication.*failed", re.IGNORECASE),
]


def is_transient_error(error: str) -> bool:
    """Classify an error message as transient (retriable).

    Permanent patterns take priority — if both match, the error is
    considered permanent.
    """
    if not error:
        return False
    if any(p.search(error) for p in PERMANENT_PATTERNS):
        return False
    return any(p.search(error) for p in TRANSIENT_PATTERNS.values())


# ---------------------------------------------------------------------------
# Exponential backoff (from OpenClaw)
# ---------------------------------------------------------------------------

BACKOFF_SCHEDULE_S: list[float] = [30, 60, 300, 900, 3600]
"""30 s → 1 min → 5 min → 15 min → 60 min.  5th+ failure stays at 60 min."""

# One-shot jobs only use the first 3 levels before giving up.
ONESHOT_MAX_TRANSIENT_RETRIES: int = 3
ONESHOT_BACKOFF_S: list[float] = BACKOFF_SCHEDULE_S[:3]  # 30 s, 1 min, 5 min


def backoff_delay(consecutive_failures: int) -> float:
    """Seconds to wait after the *N*-th consecutive failure.

    Uses the full 5-level schedule for recurring jobs.
    """
    idx = min(consecutive_failures - 1, len(BACKOFF_SCHEDULE_S) - 1)
    return BACKOFF_SCHEDULE_S[max(0, idx)]


# ---------------------------------------------------------------------------
# Delivery error classification (for DeliveryRouter retry)
# ---------------------------------------------------------------------------

_TRANSIENT_DELIVERY: list[re.Pattern[str]] = [
    re.compile(r"(econnreset|econnrefused|etimedout|enotfound)", re.IGNORECASE),
    re.compile(r"gateway not connected", re.IGNORECASE),
    re.compile(r"gateway closed", re.IGNORECASE),
    re.compile(r"network error", re.IGNORECASE),
]

_PERMANENT_DELIVERY: list[re.Pattern[str]] = [
    re.compile(r"unsupported channel", re.IGNORECASE),
    re.compile(r"unknown channel", re.IGNORECASE),
    re.compile(r"chat not found", re.IGNORECASE),
    re.compile(r"bot was blocked", re.IGNORECASE),
    re.compile(r"forbidden", re.IGNORECASE),
]

DELIVERY_RETRY_DELAYS_S: list[float] = [5, 10, 20]
"""3 retries: 5 s → 10 s → 20 s (then give up)."""


def is_transient_delivery_error(error: str) -> bool:
    """Classify a delivery error as transient (retriable)."""
    if not error:
        return False
    if any(p.search(error) for p in _PERMANENT_DELIVERY):
        return False
    return any(p.search(error) for p in _TRANSIENT_DELIVERY)
