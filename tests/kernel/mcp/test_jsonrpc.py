"""Tests for kernel.mcp.jsonrpc — JSON-RPC dispatch + reject."""

from __future__ import annotations

import asyncio
import json

import pytest

from kernel.mcp.jsonrpc import dispatch_response, reject_all_pending
from kernel.mcp.types import McpError


@pytest.fixture
def event_loop():
    """Provide an event loop for sync tests that need futures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _make_future(loop: asyncio.AbstractEventLoop) -> asyncio.Future:
    return loop.create_future()


class TestDispatchResponse:
    """dispatch_response() routing tests."""

    def test_success_response(self, event_loop: asyncio.AbstractEventLoop) -> None:
        fut = _make_future(event_loop)
        pending = {1: fut}
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()

        dispatch_response(body, pending, "test")

        assert fut.done()
        assert fut.result() == {"ok": True}
        assert 1 not in pending

    def test_error_response(self, event_loop: asyncio.AbstractEventLoop) -> None:
        fut = _make_future(event_loop)
        pending = {2: fut}
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "error": {"code": -32600, "message": "invalid"},
            }
        ).encode()

        dispatch_response(body, pending, "test")

        assert fut.done()
        with pytest.raises(McpError, match="invalid"):
            fut.result()

    def test_notification_ignored(self) -> None:
        """Messages without 'id' are notifications — dropped silently."""
        pending: dict[int, asyncio.Future] = {}
        body = json.dumps({"jsonrpc": "2.0", "method": "ping"}).encode()

        dispatch_response(body, pending, "test")  # no error

    def test_stale_id_ignored(self) -> None:
        """Response for an unknown ID (timeout/cancelled) is ignored."""
        pending: dict[int, asyncio.Future] = {}
        body = json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode()

        dispatch_response(body, pending, "test")  # no error

    def test_malformed_json_ignored(self) -> None:
        pending: dict[int, asyncio.Future] = {}
        dispatch_response(b"not json", pending, "test")  # no error

    def test_already_done_future_ignored(self, event_loop: asyncio.AbstractEventLoop) -> None:
        """If future was already cancelled/timed out, skip it."""
        fut = _make_future(event_loop)
        fut.cancel()
        pending = {3: fut}
        body = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {}}).encode()

        dispatch_response(body, pending, "test")  # no error


class TestRejectAllPending:
    """reject_all_pending() tests."""

    def test_rejects_all_and_clears(self, event_loop: asyncio.AbstractEventLoop) -> None:
        f1 = _make_future(event_loop)
        f2 = _make_future(event_loop)
        pending = {1: f1, 2: f2}

        reject_all_pending(pending, "closing")

        assert f1.done()
        assert f2.done()
        assert len(pending) == 0
        with pytest.raises(McpError, match="closing"):
            f1.result()

    def test_skips_already_done(self, event_loop: asyncio.AbstractEventLoop) -> None:
        fut = _make_future(event_loop)
        fut.set_result("ok")
        pending = {1: fut}

        reject_all_pending(pending, "closing")

        assert fut.result() == "ok"  # not overwritten
        assert len(pending) == 0
