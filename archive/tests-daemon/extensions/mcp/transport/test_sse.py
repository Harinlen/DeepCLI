"""Tests for the SSE transport.

Uses a local HTTP server (via httpx mock transport) to simulate
an MCP SSE endpoint without requiring a real remote server.
"""

from __future__ import annotations

import json

import pytest

from daemon.extensions.mcp.transport.base import TransportClosed
from daemon.extensions.mcp.transport.sse import SseTransport


class TestSseTransportProperties:
    """Basic property tests."""

    def test_not_connected_initially(self) -> None:
        t = SseTransport(url="http://localhost:9999/sse", name="test")
        assert not t.is_connected

    @pytest.mark.asyncio
    async def test_close_idempotent(self) -> None:
        """Closing a never-connected transport is a no-op."""
        t = SseTransport(url="http://localhost:9999/sse", name="test")
        await t.close()
        assert not t.is_connected


class TestSseTransportErrors:
    """Tests for error paths."""

    @pytest.mark.asyncio
    async def test_send_not_connected(self) -> None:
        """send() raises TransportClosed when not connected."""
        t = SseTransport(url="http://localhost:9999/sse", name="test")
        with pytest.raises(TransportClosed):
            await t.send(b'{"jsonrpc": "2.0"}')

    @pytest.mark.asyncio
    async def test_receive_not_connected(self) -> None:
        """receive() raises TransportClosed when not connected."""
        t = SseTransport(url="http://localhost:9999/sse", name="test")
        with pytest.raises(TransportClosed):
            await t.receive()


class TestSseTransportEventParsing:
    """Test SSE event parsing logic directly."""

    def test_handle_endpoint_event(self) -> None:
        """endpoint event sets the POST URL."""
        t = SseTransport(url="http://example.com/sse", name="test")
        t._handle_sse_event("endpoint", "http://example.com/message")
        assert t._endpoint_url == "http://example.com/message"

    def test_handle_endpoint_relative_url(self) -> None:
        """Relative endpoint URL is resolved against base."""
        t = SseTransport(url="http://example.com/sse", name="test")
        t._handle_sse_event("endpoint", "/message")
        assert t._endpoint_url == "http://example.com/message"

    def test_handle_message_event(self) -> None:
        """message event enqueues data as bytes."""
        t = SseTransport(url="http://example.com/sse", name="test")
        t._handle_sse_event("message", '{"result": "ok"}')
        assert not t._event_queue.empty()
        data = t._event_queue.get_nowait()
        assert json.loads(data) == {"result": "ok"}

    def test_handle_default_event_type(self) -> None:
        """Empty event type defaults to message behavior."""
        t = SseTransport(url="http://example.com/sse", name="test")
        t._handle_sse_event("", '{"id": 1}')
        assert not t._event_queue.empty()


class TestSseTransportEndToEnd:
    """End-to-end test with a local SSE server."""

    @pytest.mark.asyncio
    async def test_connect_fails_on_unreachable(self) -> None:
        """Connect fails when server is unreachable.

        The SSE stream connection error causes the listen task to
        exit, which unblocks connect() without an endpoint — resulting
        in a TransportClosed error.
        """
        t = SseTransport(
            url="http://127.0.0.1:19876/sse",
            name="unreachable",
        )
        # Short timeout to keep the test fast
        import daemon.extensions.mcp.transport.sse as sse_mod

        original = sse_mod._ENDPOINT_DISCOVERY_TIMEOUT
        sse_mod._ENDPOINT_DISCOVERY_TIMEOUT = 2.0
        try:
            with pytest.raises(TransportClosed):
                await t.connect()
        finally:
            sse_mod._ENDPOINT_DISCOVERY_TIMEOUT = original
