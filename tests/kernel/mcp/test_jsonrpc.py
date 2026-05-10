"""Tests for kernel.agents.mustang.mcp.jsonrpc — JSON-RPC dispatch + reject."""

from __future__ import annotations

import json
from concurrent.futures import Future

import pytest

from kernel.agents.mustang.mcp.jsonrpc import dispatch_response, reject_all_pending
from kernel.agents.mustang.mcp.types import McpError


def _make_future() -> Future:
    return Future()


class TestDispatchResponse:
    """dispatch_response() routing tests."""

    def test_success_response(self) -> None:
        fut = _make_future()
        pending = {1: fut}
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()

        dispatch_response(body, pending, "test")

        assert fut.done()
        assert fut.result() == {"ok": True}
        assert 1 not in pending

    def test_error_response(self) -> None:
        fut = _make_future()
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
        pending: dict[int, Future] = {}
        body = json.dumps({"jsonrpc": "2.0", "method": "ping"}).encode()

        dispatch_response(body, pending, "test")  # no error

    def test_stale_id_ignored(self) -> None:
        """Response for an unknown ID (timeout/cancelled) is ignored."""
        pending: dict[int, Future] = {}
        body = json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode()

        dispatch_response(body, pending, "test")  # no error

    def test_malformed_json_ignored(self) -> None:
        pending: dict[int, Future] = {}
        dispatch_response(b"not json", pending, "test")  # no error

    def test_already_done_future_ignored(self) -> None:
        """If future was already cancelled/timed out, skip it."""
        fut = _make_future()
        fut.cancel()
        pending = {3: fut}
        body = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {}}).encode()

        dispatch_response(body, pending, "test")  # no error


class TestRejectAllPending:
    """reject_all_pending() tests."""

    def test_rejects_all_and_clears(self) -> None:
        f1 = _make_future()
        f2 = _make_future()
        pending = {1: f1, 2: f2}

        reject_all_pending(pending, "closing")

        assert f1.done()
        assert f2.done()
        assert len(pending) == 0
        with pytest.raises(McpError, match="closing"):
            f1.result()
        with pytest.raises(McpError, match="closing"):
            f2.result()

    def test_skips_already_done(self) -> None:
        fut = _make_future()
        fut.set_result("ok")
        pending = {1: fut}

        reject_all_pending(pending, "closing")

        assert fut.result() == "ok"  # not overwritten
        assert len(pending) == 0
