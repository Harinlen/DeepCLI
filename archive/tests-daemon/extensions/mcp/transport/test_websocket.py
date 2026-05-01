"""Tests for the WebSocket transport.

Tests basic properties and error paths.  End-to-end tests use a
local WebSocket server via the ``websockets`` library to verify
the full connect → send → receive → close lifecycle.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import websockets
import websockets.asyncio.server

from daemon.extensions.mcp.transport.base import TransportClosed
from daemon.extensions.mcp.transport.websocket import WebSocketTransport


class TestWebSocketTransportProperties:
    """Basic property tests."""

    def test_not_connected_initially(self) -> None:
        t = WebSocketTransport(url="ws://localhost:9999", name="test")
        assert not t.is_connected

    @pytest.mark.asyncio
    async def test_close_idempotent(self) -> None:
        """Closing a never-connected transport is a no-op."""
        t = WebSocketTransport(url="ws://localhost:9999", name="test")
        await t.close()
        assert not t.is_connected


class TestWebSocketTransportErrors:
    """Tests for error paths."""

    @pytest.mark.asyncio
    async def test_send_not_connected(self) -> None:
        t = WebSocketTransport(url="ws://localhost:9999", name="test")
        with pytest.raises(TransportClosed):
            await t.send(b'{"jsonrpc": "2.0"}')

    @pytest.mark.asyncio
    async def test_receive_not_connected(self) -> None:
        t = WebSocketTransport(url="ws://localhost:9999", name="test")
        with pytest.raises(TransportClosed):
            await t.receive()

    @pytest.mark.asyncio
    async def test_connect_unreachable(self) -> None:
        """Connect fails on unreachable server."""
        t = WebSocketTransport(url="ws://127.0.0.1:19877/ws", name="unreachable")
        # Patch timeout to be short
        import daemon.extensions.mcp.transport.websocket as ws_mod

        original = ws_mod._CONNECT_TIMEOUT
        ws_mod._CONNECT_TIMEOUT = 1.0
        try:
            with pytest.raises(TransportClosed, match="connect failed"):
                await t.connect()
        finally:
            ws_mod._CONNECT_TIMEOUT = original


# ------------------------------------------------------------------
# End-to-end tests with a local WebSocket echo MCP server
# ------------------------------------------------------------------


async def _echo_mcp_handler(
    websocket: websockets.asyncio.server.ServerConnection,
) -> None:
    """Simple WS handler that implements MCP initialize + echo tool."""
    async for raw in websocket:
        msg = json.loads(raw)
        method = msg.get("method", "")
        req_id = msg.get("id")

        match method:
            case "initialize":
                resp: dict[str, Any] = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "ws-echo"},
                    },
                }
            case "notifications/initialized":
                continue
            case "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                    },
                                },
                            }
                        ]
                    },
                }
            case "tools/call":
                params = msg.get("params", {})
                text = params.get("arguments", {}).get("text", "")
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                    },
                }
            case _:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "Not found"},
                }

        await websocket.send(json.dumps(resp))


@pytest.fixture
async def ws_server():
    """Start a local WebSocket MCP server for testing."""
    server = await websockets.asyncio.server.serve(
        _echo_mcp_handler,
        "127.0.0.1",
        0,  # OS-assigned port
    )
    port = server.sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}"
    server.close()
    await server.wait_closed()


class TestWebSocketEndToEnd:
    """End-to-end tests with a real local WebSocket server."""

    @pytest.mark.asyncio
    async def test_connect_and_send_receive(self, ws_server: str) -> None:
        """Full lifecycle: connect → send → receive → close."""
        t = WebSocketTransport(url=ws_server, name="e2e")

        await t.connect()
        assert t.is_connected

        try:
            # Send initialize
            req = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test"},
                    },
                }
            ).encode()
            await t.send(req)
            resp = json.loads(await t.receive())

            assert resp["id"] == 1
            assert resp["result"]["serverInfo"]["name"] == "ws-echo"
        finally:
            await t.close()

        assert not t.is_connected

    @pytest.mark.asyncio
    async def test_full_mcp_flow_with_client(self, ws_server: str) -> None:
        """McpClient → WebSocketTransport → local WS server."""
        from daemon.extensions.mcp.client import McpClient

        t = WebSocketTransport(url=ws_server, name="e2e")
        client = McpClient(t, server_name="ws-echo")

        await client.connect()
        try:
            assert client.is_connected
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0]["name"] == "echo"

            result = await client.call_tool("echo", {"text": "ws test"})
            assert result["content"][0]["text"] == "ws test"
        finally:
            await client.close()
