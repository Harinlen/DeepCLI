"""Tests for error classification and backoff."""

from __future__ import annotations

import pytest

from kernel.schedule.errors import (
    BACKOFF_SCHEDULE_S,
    backoff_delay,
    is_transient_delivery_error,
    is_transient_error,
)


class TestTransientClassification:
    """is_transient_error boundary cases."""

    @pytest.mark.parametrize(
        "msg",
        [
            "429 Too Many Requests",
            "rate_limit exceeded",
            "Request rate limit reached",
            "overloaded_error",
            "Server is temporarily overloaded",
            "network error: ECONNRESET",
            "fetch failed: socket hang up",
            "Request timeout after 30s",
            "ETIMEDOUT",
            "502 Bad Gateway",
            "503 Service Unavailable",
        ],
    )
    def test_transient_patterns(self, msg: str) -> None:
        assert is_transient_error(msg), f"expected transient: {msg!r}"

    @pytest.mark.parametrize(
        "msg",
        [
            "forbidden: bot not authorized",
            "unsupported channel type",
            "chat not found",
            "bot was not member of channel",
            "invalid api_key provided",
            "authentication failed",
        ],
    )
    def test_permanent_patterns(self, msg: str) -> None:
        assert not is_transient_error(msg), f"expected permanent: {msg!r}"

    def test_empty_string(self) -> None:
        assert not is_transient_error("")

    def test_permanent_takes_priority(self) -> None:
        """When both transient and permanent match, permanent wins."""
        # "forbidden" is permanent; "timeout" is transient
        assert not is_transient_error("forbidden timeout")


class TestBackoff:
    """backoff_delay schedule verification."""

    def test_five_levels(self) -> None:
        assert backoff_delay(1) == 30
        assert backoff_delay(2) == 60
        assert backoff_delay(3) == 300
        assert backoff_delay(4) == 900
        assert backoff_delay(5) == 3600

    def test_clamps_at_max(self) -> None:
        """6th+ failure stays at the 5th level (60 min)."""
        assert backoff_delay(6) == 3600
        assert backoff_delay(100) == 3600

    def test_zero_failures(self) -> None:
        """Edge case: 0 failures shouldn't crash."""
        assert backoff_delay(0) == BACKOFF_SCHEDULE_S[0]


class TestDeliveryErrorClassification:
    """is_transient_delivery_error tests."""

    def test_transient(self) -> None:
        assert is_transient_delivery_error("ECONNRESET")
        assert is_transient_delivery_error("gateway not connected")

    def test_permanent(self) -> None:
        assert not is_transient_delivery_error("chat not found")
        assert not is_transient_delivery_error("bot was blocked by user")

    def test_empty(self) -> None:
        assert not is_transient_delivery_error("")
