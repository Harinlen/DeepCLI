"""Tests for the transport-agnostic MCP client.

Tests JSON-RPC dispatch logic with a mock transport and verifies
the client's lifecycle (connect, close, reconnect) contract.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from daemon.errors import McpError
from daemon.extensions.mcp.client import McpClient
from daemon.extensions.mcp.transport.base import Transport, TransportClosed
from daemon.extensions.mcp.transport.stdio import _read_content_length


class MockTransport(Transport):
    """In-memory transport for testing McpClient."""

    def __init__(self) -> None:
        self._connected = False
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self._sent: list[bytes] = []

    async def connect(self) -> None:
        self._connected = True

    async def send(self, message: bytes) -> None:
        if not self._connected:
            raise TransportClosed("not connected")
        self._sent.append(message)

    async def receive(self) -> bytes:
        if not self._connected:
            raise TransportClosed("not connected")
        try:
            return await self._inbox.get()
        except asyncio.CancelledError:
            raise TransportClosed("cancelled") from None

    async def close(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def feed_response(self, msg: dict) -> None:
        """Enqueue a JSON-RPC response for the client to read."""
        self._inbox.put_nowait(json.dumps(msg).encode())


def _make_transport() -> MockTransport:
    return MockTransport()


def _make_client(
    transport: MockTransport | None = None,
    name: str = "test-server",
) -> tuple[McpClient, MockTransport]:
    t = transport or _make_transport()
    client = McpClient(t, server_name=name)
    return client, t


class TestMcpClientProperties:
    """Tests for McpClient basic properties."""

    def test_server_name(self) -> None:
        client, _ = _make_client(name="my-server")
        assert client.server_name == "my-server"

    def test_is_connected_default(self) -> None:
        client, _ = _make_client()
        assert client.is_connected is False

    def test_server_capabilities_default(self) -> None:
        client, _ = _make_client()
        assert client.server_capabilities == {}

    def test_transport_property(self) -> None:
        transport = _make_transport()
        client, t = _make_client(transport=transport)
        assert client.transport is transport


class TestDispatchMessage:
    """Tests for JSON-RPC message dispatching (internal method)."""

    def test_dispatch_response(self) -> None:
        """Response with matching id resolves the future."""
        client, _ = _make_client()

        loop = asyncio.new_event_loop()
        future = loop.create_future()
        client._pending[42] = future

        body = json.dumps({"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}).encode()
        client._dispatch_message(body)

        assert future.done()
        assert future.result() == {"ok": True}
        loop.close()

    def test_dispatch_error_response(self) -> None:
        """Error response sets McpError on the future."""
        client, _ = _make_client()

        loop = asyncio.new_event_loop()
        future = loop.create_future()
        client._pending[7] = future

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "error": {"code": -32600, "message": "Invalid request"},
            }
        ).encode()
        client._dispatch_message(body)

        assert future.done()
        with pytest.raises(McpError, match="Invalid request"):
            future.result()
        loop.close()

    def test_dispatch_notification_ignored(self) -> None:
        """Notifications (no id) don't affect pending futures."""
        client, _ = _make_client()
        body = json.dumps({"jsonrpc": "2.0", "method": "some/notification"}).encode()
        client._dispatch_message(body)  # Should not raise

    def test_dispatch_unknown_id_ignored(self) -> None:
        """Response with unknown id is silently ignored."""
        client, _ = _make_client()
        body = json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode()
        client._dispatch_message(body)  # Should not raise

    def test_dispatch_invalid_json(self) -> None:
        """Invalid JSON is logged and ignored."""
        client, _ = _make_client()
        client._dispatch_message(b"not json")  # Should not raise

    def test_dispatch_result_missing(self) -> None:
        """Response without 'result' key defaults to empty dict."""
        client, _ = _make_client()

        loop = asyncio.new_event_loop()
        future = loop.create_future()
        client._pending[1] = future

        body = json.dumps({"jsonrpc": "2.0", "id": 1}).encode()
        client._dispatch_message(body)

        assert future.result() == {}
        loop.close()


class TestSendRequest:
    """Tests for _send_request error handling."""

    @pytest.mark.asyncio
    async def test_send_request_not_connected(self) -> None:
        """Raises McpError when transport is not connected."""
        client, _ = _make_client()
        with pytest.raises(McpError, match="not running"):
            await client._send_request("test/method")

    @pytest.mark.asyncio
    async def test_send_notification_not_connected(self) -> None:
        """Raises McpError when transport is not connected."""
        client, _ = _make_client()
        with pytest.raises(McpError, match="not running"):
            await client._send_notification("test/notify")

    @pytest.mark.asyncio
    async def test_send_request_transport_closed(self) -> None:
        """Raises McpError when transport closes during send."""
        client, transport = _make_client()
        # Manually set connected state (skip handshake)
        transport._connected = True

        # Make transport raise on send
        async def _fail_send(msg: bytes) -> None:
            raise TransportClosed("pipe broken")

        transport.send = _fail_send  # type: ignore[assignment]

        with pytest.raises(McpError, match="transport closed"):
            await client._send_request("test/method")


class TestHandleTransportClosed:
    """Tests for the public handle_transport_closed method."""

    @pytest.mark.asyncio
    async def test_marks_disconnected(self) -> None:
        """Client is marked disconnected after transport closure."""
        client, transport = _make_client()
        client._connected = True
        client._max_reconnect_attempts = 0  # Don't actually reconnect

        await client.handle_transport_closed("test reason")

        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_rejects_pending(self) -> None:
        """Pending futures are rejected with the reason."""
        client, _ = _make_client()
        client._connected = True
        client._max_reconnect_attempts = 0

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        client._pending[1] = future

        await client.handle_transport_closed("connection lost")

        assert future.done()
        with pytest.raises(McpError, match="connection lost"):
            future.result()

    @pytest.mark.asyncio
    async def test_no_op_when_closing(self) -> None:
        """No-op when client is already closing."""
        client, _ = _make_client()
        client._connected = True
        client._closing = True

        await client.handle_transport_closed("test")

        # Should still be "connected" since the method was a no-op
        assert client._connected is True


class TestClose:
    """Tests for close() lifecycle."""

    @pytest.mark.asyncio
    async def test_close_idempotent(self) -> None:
        """Closing an unconnected client is a no-op."""
        client, _ = _make_client()
        await client.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_close_stops_transport(self) -> None:
        """close() delegates to transport.close()."""
        client, transport = _make_client()
        transport._connected = True

        await client.close()

        assert not transport.is_connected


class TestReadContentLength:
    """Tests for the stdio Content-Length header parser."""

    @pytest.mark.asyncio
    async def test_parses_content_length(self) -> None:
        """Correctly parses Content-Length from headers."""
        data = b"Content-Length: 42\r\n\r\n"
        reader = asyncio.StreamReader()
        reader.feed_data(data)

        result = await _read_content_length(reader)
        assert result == 42

    @pytest.mark.asyncio
    async def test_eof(self) -> None:
        """Returns None on EOF."""
        reader = asyncio.StreamReader()
        reader.feed_eof()

        result = await _read_content_length(reader)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_content_length(self) -> None:
        """Raises McpError when Content-Length is missing."""
        data = b"X-Custom: value\r\n\r\n"
        reader = asyncio.StreamReader()
        reader.feed_data(data)

        with pytest.raises(McpError, match="Missing Content-Length"):
            await _read_content_length(reader)
