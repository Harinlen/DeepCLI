"""Tests for the StdioTransport implementation."""

from __future__ import annotations

import asyncio

import pytest

from daemon.errors import McpError
from daemon.extensions.mcp.config import McpServerEntry
from daemon.extensions.mcp.transport.base import TransportClosed
from daemon.extensions.mcp.transport.stdio import (
    StdioTransport,
    _read_content_length,
)


def _make_entry(name: str = "test", command: str = "echo") -> McpServerEntry:
    return McpServerEntry(name=name, type="stdio", command=command)


class TestStdioTransportProperties:
    """Basic property tests."""

    def test_not_connected_initially(self) -> None:
        t = StdioTransport(_make_entry())
        assert not t.is_connected

    def test_stderr_tail_empty(self) -> None:
        t = StdioTransport(_make_entry())
        assert t.stderr_tail == "(empty)"


class TestStdioTransportConnect:
    """Tests for connect / close lifecycle."""

    @pytest.mark.asyncio
    async def test_file_not_found(self) -> None:
        """McpError on missing command."""
        entry = McpServerEntry(name="bad", type="stdio", command="/nonexistent/path/mcp")
        t = StdioTransport(entry)
        with pytest.raises(McpError, match="Failed to start"):
            await t.connect()

    @pytest.mark.asyncio
    async def test_close_idempotent(self) -> None:
        """Closing a never-connected transport is a no-op."""
        t = StdioTransport(_make_entry())
        await t.close()  # Should not raise
        assert not t.is_connected


class TestStdioTransportSend:
    """Tests for send() framing."""

    @pytest.mark.asyncio
    async def test_send_not_connected(self) -> None:
        """send() raises TransportClosed when not connected."""
        t = StdioTransport(_make_entry())
        with pytest.raises(TransportClosed):
            await t.send(b'{"jsonrpc": "2.0"}')


class TestStdioTransportReceive:
    """Tests for receive() framing."""

    @pytest.mark.asyncio
    async def test_receive_not_connected(self) -> None:
        """receive() raises TransportClosed when not connected."""
        t = StdioTransport(_make_entry())
        with pytest.raises(TransportClosed):
            await t.receive()


class TestReadContentLength:
    """Tests for the Content-Length header parser (extracted helper)."""

    @pytest.mark.asyncio
    async def test_parses_content_length(self) -> None:
        data = b"Content-Length: 42\r\n\r\n"
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        assert await _read_content_length(reader) == 42

    @pytest.mark.asyncio
    async def test_eof_returns_none(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        assert await _read_content_length(reader) is None

    @pytest.mark.asyncio
    async def test_missing_content_length(self) -> None:
        data = b"X-Custom: value\r\n\r\n"
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        with pytest.raises(McpError, match="Missing Content-Length"):
            await _read_content_length(reader)

    @pytest.mark.asyncio
    async def test_case_insensitive(self) -> None:
        """Content-Length header parsing is case-insensitive."""
        data = b"content-length: 100\r\n\r\n"
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        assert await _read_content_length(reader) == 100

    @pytest.mark.asyncio
    async def test_extra_headers_ignored(self) -> None:
        """Non-Content-Length headers are silently skipped."""
        data = b"X-Extra: foo\r\nContent-Length: 7\r\n\r\n"
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        assert await _read_content_length(reader) == 7
