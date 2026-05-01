"""Tests for the WebSocket PermissionHandler."""

from __future__ import annotations


import pytest

from daemon.api.permission_handler import PermissionHandler
from daemon.engine.stream import PermissionResponse


def _resp(request_id: str, decision: str) -> PermissionResponse:
    """Build a :class:`PermissionResponse` for test brevity."""
    return PermissionResponse(request_id=request_id, decision=decision)  # type: ignore[arg-type]


class TestPermissionHandler:
    """Tests for PermissionHandler."""

    @pytest.mark.asyncio
    async def test_create_and_resolve(self) -> None:
        """Create a waiter, resolve it, and await the result."""
        h = PermissionHandler()
        waiter = h.create_waiter("req1")
        assert not waiter.done()

        assert h.resolve("req1", _resp("req1", "allow"))
        result = await waiter
        assert result.decision == "allow"

    @pytest.mark.asyncio
    async def test_resolve_deny(self) -> None:
        """Denied permission resolves to a deny response."""
        h = PermissionHandler()
        waiter = h.create_waiter("req1")
        h.resolve("req1", _resp("req1", "deny"))
        result = await waiter
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_resolve_always_allow(self) -> None:
        """``always_allow`` decision is preserved end-to-end."""
        h = PermissionHandler()
        waiter = h.create_waiter("req1")
        h.resolve("req1", _resp("req1", "always_allow"))
        result = await waiter
        assert result.decision == "always_allow"

    @pytest.mark.asyncio
    async def test_resolve_unknown_returns_false(self) -> None:
        """Resolving a non-existent request returns False."""
        h = PermissionHandler()
        assert h.resolve("nonexistent", _resp("nonexistent", "allow")) is False

    @pytest.mark.asyncio
    async def test_has_pending(self) -> None:
        """has_pending reflects outstanding requests."""
        h = PermissionHandler()
        assert not h.has_pending

        h.create_waiter("req1")
        assert h.has_pending

        h.resolve("req1", _resp("req1", "allow"))
        assert not h.has_pending

    @pytest.mark.asyncio
    async def test_cancel_all(self) -> None:
        """cancel_all resolves all pending as denied."""
        h = PermissionHandler()
        w1 = h.create_waiter("req1")
        w2 = h.create_waiter("req2")

        h.cancel_all()

        r1 = await w1
        r2 = await w2
        assert r1.decision == "deny"
        assert r2.decision == "deny"
        assert not h.has_pending

    @pytest.mark.asyncio
    async def test_double_resolve_ignored(self) -> None:
        """Second resolve for same request is a no-op."""
        h = PermissionHandler()
        h.create_waiter("req1")

        assert h.resolve("req1", _resp("req1", "allow"))
        # Already popped — second resolve returns False
        assert h.resolve("req1", _resp("req1", "deny")) is False
