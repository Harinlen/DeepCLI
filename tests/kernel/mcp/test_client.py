"""Tests for kernel.mcp.client — McpClient protocol + reconnection."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from kernel.mcp.client import McpClient
from kernel.mcp.types import McpError, McpToolCallError, TransportClosed


# ── Fake transport ──────────────────────────────────────────────────


class FakeTransport:
    """In-memory transport that feeds scripted responses."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self._responses = list(responses or [])
        self._sent: list[dict[str, Any]] = []
        self._connected = False
        self._recv_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def connect(self) -> None:
        self._connected = True

    async def send(self, message: bytes) -> None:
        if not self._connected:
            raise TransportClosed("not connected")
        parsed = json.loads(message)
        self._sent.append(parsed)

        # Only respond to requests (have "id"), not notifications.
        if "id" in parsed and self._responses:
            resp = self._responses.pop(0)
            resp["id"] = parsed["id"]
            self._recv_queue.put_nowait(json.dumps(resp).encode())

    async def receive(self) -> bytes:
        # Block until a message arrives or transport is closed.
        # No timeout — the read loop stays alive until close() is called.
        while True:
            if not self._connected:
                raise TransportClosed("not connected")
            try:
                return await asyncio.wait_for(self._recv_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue  # check _connected again

    async def close(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


def _init_response(caps: dict | None = None) -> dict:
    """Scripted initialize response."""
    return {
        "jsonrpc": "2.0",
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": caps or {"tools": {}},
            "serverInfo": {"name": "test-server", "version": "1.0"},
        },
    }


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_connect_handshake() -> None:
    """connect() performs initialize + initialized handshake."""
    transport = FakeTransport(responses=[_init_response({"tools": {}, "resources": {}})])
    client = McpClient(transport, server_name="test", max_reconnect_attempts=0)

    caps = await client.connect()

    assert client.is_connected
    assert "tools" in caps
    assert client.server_info == {"name": "test-server", "version": "1.0"}

    # First sent message should be 'initialize'.
    assert transport._sent[0]["method"] == "initialize"
    # Second should be 'initialized' notification (no id).
    assert transport._sent[1]["method"] == "initialized"
    assert "id" not in transport._sent[1]

    await client.close()


@pytest.mark.anyio
async def test_list_tools() -> None:
    """list_tools() returns parsed McpToolDef list."""
    tools_response = {
        "jsonrpc": "2.0",
        "result": {
            "tools": [
                {"name": "echo", "description": "echo input", "inputSchema": {"type": "object"}},
                {"name": "add", "description": "add numbers"},
            ],
        },
    }
    transport = FakeTransport(responses=[_init_response(), tools_response])
    client = McpClient(transport, server_name="test", max_reconnect_attempts=0)
    await client.connect()

    tools = await client.list_tools()

    assert len(tools) == 2
    assert tools[0].name == "echo"
    assert tools[0].description == "echo input"
    assert tools[1].name == "add"

    await client.close()


@pytest.mark.anyio
async def test_call_tool_success() -> None:
    """call_tool() returns McpToolResult on success."""
    call_response = {
        "jsonrpc": "2.0",
        "result": {
            "content": [{"type": "text", "text": "hello"}],
            "isError": False,
        },
    }
    transport = FakeTransport(responses=[_init_response(), call_response])
    client = McpClient(transport, server_name="test", max_reconnect_attempts=0)
    await client.connect()

    result = await client.call_tool("echo", {"msg": "hi"})

    assert not result.is_error
    assert result.content[0]["text"] == "hello"

    await client.close()


@pytest.mark.anyio
async def test_call_tool_error() -> None:
    """call_tool() raises McpToolCallError when isError=true."""
    call_response = {
        "jsonrpc": "2.0",
        "result": {
            "content": [{"type": "text", "text": "bad input"}],
            "isError": True,
        },
    }
    transport = FakeTransport(responses=[_init_response(), call_response])
    client = McpClient(transport, server_name="test", max_reconnect_attempts=0)
    await client.connect()

    with pytest.raises(McpToolCallError, match="bad input"):
        await client.call_tool("echo", {"msg": "hi"})

    await client.close()


@pytest.mark.anyio
async def test_request_timeout() -> None:
    """Requests that exceed timeout raise McpError."""
    # Transport that never responds after handshake.
    transport = FakeTransport(responses=[_init_response()])
    client = McpClient(transport, server_name="test", request_timeout=0.1, max_reconnect_attempts=0)
    await client.connect()

    with pytest.raises(McpError, match="timed out"):
        await client.list_tools()

    await client.close()


@pytest.mark.anyio
async def test_close_rejects_pending() -> None:
    """close() rejects all in-flight requests."""
    transport = FakeTransport(responses=[_init_response()])
    client = McpClient(transport, server_name="test", request_timeout=5.0, max_reconnect_attempts=0)
    await client.connect()

    # Start a request that will never get a response.
    task = asyncio.create_task(client.list_tools())
    await asyncio.sleep(0.05)  # let request send

    await client.close()

    with pytest.raises(McpError, match="closing"):
        await task


@pytest.mark.anyio
async def test_close_idempotent() -> None:
    """Multiple close() calls don't raise."""
    transport = FakeTransport(responses=[_init_response()])
    client = McpClient(transport, server_name="test", max_reconnect_attempts=0)
    await client.connect()

    await client.close()
    await client.close()  # no error
