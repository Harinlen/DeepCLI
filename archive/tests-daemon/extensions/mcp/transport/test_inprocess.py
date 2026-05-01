"""Tests for the InProcessTransport + McpServerProtocol.

Includes an echo MCP server that implements the full initialize
handshake and tools/list + tools/call, verifying the transport
end-to-end with the real McpClient.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from daemon.extensions.mcp.client import McpClient
from daemon.extensions.mcp.server.protocol import McpServerProtocol
from daemon.extensions.mcp.transport.base import TransportClosed
from daemon.extensions.mcp.transport.inprocess import InProcessTransport


# ------------------------------------------------------------------
# Test MCP servers
# ------------------------------------------------------------------


class EchoServer(McpServerProtocol):
    """Minimal MCP server that echoes tool call arguments back.

    Implements the full MCP handshake (initialize/initialized) and
    exposes one tool: ``echo(text: str) -> text``.
    """

    async def run(
        self,
        inbox: asyncio.Queue[bytes],
        outbox: asyncio.Queue[bytes],
    ) -> None:
        try:
            while True:
                raw = await inbox.get()
                request = json.loads(raw)
                response = self._handle(request)
                if response is not None:
                    await outbox.put(json.dumps(response).encode())
        except asyncio.CancelledError:
            pass

    def _handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method", "")
        req_id = request.get("id")

        match method:
            case "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": self.capabilities(),
                        "serverInfo": {"name": "echo-server"},
                    },
                }
            case "notifications/initialized":
                return None  # Notification — no response
            case "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo the input text",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                    },
                                    "required": ["text"],
                                },
                            },
                        ]
                    },
                }
            case "tools/call":
                params = request.get("params", {})
                tool_name = params.get("name", "")
                args = params.get("arguments", {})

                if tool_name == "echo":
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {"type": "text", "text": args.get("text", "")},
                            ]
                        },
                    }

                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unknown tool: {tool_name}",
                    },
                }
            case _:
                if req_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    }
                return None

    def capabilities(self) -> dict[str, Any]:
        return {"tools": {}}


class CalculatorServer(McpServerProtocol):
    """MCP server with an ``add`` tool for arithmetic testing."""

    async def run(
        self,
        inbox: asyncio.Queue[bytes],
        outbox: asyncio.Queue[bytes],
    ) -> None:
        try:
            while True:
                raw = await inbox.get()
                request = json.loads(raw)
                response = self._handle(request)
                if response is not None:
                    await outbox.put(json.dumps(response).encode())
        except asyncio.CancelledError:
            pass

    def _handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method", "")
        req_id = request.get("id")

        match method:
            case "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": self.capabilities(),
                        "serverInfo": {"name": "calculator"},
                    },
                }
            case "notifications/initialized":
                return None
            case "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "add",
                                "description": "Add two numbers",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "a": {"type": "number"},
                                        "b": {"type": "number"},
                                    },
                                    "required": ["a", "b"],
                                },
                            },
                        ]
                    },
                }
            case "tools/call":
                params = request.get("params", {})
                args = params.get("arguments", {})
                result_val = args.get("a", 0) + args.get("b", 0)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": str(result_val)},
                        ]
                    },
                }
            case _:
                return None

    def capabilities(self) -> dict[str, Any]:
        return {"tools": {}}


# ------------------------------------------------------------------
# Transport-level tests
# ------------------------------------------------------------------


class TestInProcessTransport:
    """Tests for InProcessTransport lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_disconnect(self) -> None:
        """Basic lifecycle: connect → connected → close → disconnected."""
        transport = InProcessTransport(EchoServer, name="test")
        assert not transport.is_connected

        await transport.connect()
        assert transport.is_connected

        await transport.close()
        assert not transport.is_connected

    @pytest.mark.asyncio
    async def test_close_idempotent(self) -> None:
        """Closing twice does not raise."""
        transport = InProcessTransport(EchoServer, name="test")
        await transport.connect()
        await transport.close()
        await transport.close()

    @pytest.mark.asyncio
    async def test_send_when_closed(self) -> None:
        """send() raises TransportClosed when not connected."""
        transport = InProcessTransport(EchoServer, name="test")
        with pytest.raises(TransportClosed):
            await transport.send(b"hello")

    @pytest.mark.asyncio
    async def test_receive_when_closed(self) -> None:
        """receive() raises TransportClosed when not connected."""
        transport = InProcessTransport(EchoServer, name="test")
        with pytest.raises(TransportClosed):
            await transport.receive()

    @pytest.mark.asyncio
    async def test_send_receive_roundtrip(self) -> None:
        """Messages sent arrive at the server and responses come back."""
        transport = InProcessTransport(EchoServer, name="test")
        await transport.connect()

        try:
            # Send an initialize request
            req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test"},
                },
            }
            await transport.send(json.dumps(req).encode())
            resp_bytes = await transport.receive()
            resp = json.loads(resp_bytes)

            assert resp["id"] == 1
            assert "result" in resp
            assert resp["result"]["serverInfo"]["name"] == "echo-server"
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_reconnect(self) -> None:
        """close → connect works (fresh queues each time)."""
        transport = InProcessTransport(EchoServer, name="test")

        await transport.connect()
        await transport.close()

        # Reconnect
        await transport.connect()
        assert transport.is_connected

        # Should work after reconnect
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test"},
            },
        }
        await transport.send(json.dumps(req).encode())
        resp_bytes = await transport.receive()
        resp = json.loads(resp_bytes)
        assert resp["id"] == 1

        await transport.close()


# ------------------------------------------------------------------
# End-to-end tests with McpClient
# ------------------------------------------------------------------


class TestInProcessEndToEnd:
    """End-to-end tests: McpClient + InProcessTransport + server."""

    @pytest.mark.asyncio
    async def test_echo_server_connect_and_list_tools(self) -> None:
        """Full handshake and tool discovery with EchoServer."""
        transport = InProcessTransport(EchoServer, name="echo")
        client = McpClient(transport, server_name="echo")

        await client.connect()
        try:
            assert client.is_connected
            assert client.server_capabilities == {"tools": {}}

            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0]["name"] == "echo"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_echo_server_call_tool(self) -> None:
        """Invoke echo tool and verify the result."""
        transport = InProcessTransport(EchoServer, name="echo")
        client = McpClient(transport, server_name="echo")

        await client.connect()
        try:
            result = await client.call_tool("echo", {"text": "hello world"})
            content = result.get("content", [])
            assert len(content) == 1
            assert content[0]["text"] == "hello world"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_calculator_server_add(self) -> None:
        """Invoke calculator add tool end-to-end."""
        transport = InProcessTransport(CalculatorServer, name="calc")
        client = McpClient(transport, server_name="calc")

        await client.connect()
        try:
            result = await client.call_tool("add", {"a": 3, "b": 7})
            content = result.get("content", [])
            assert content[0]["text"] == "10"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_calculator_list_and_call(self) -> None:
        """Full flow: connect → list tools → call tool."""
        transport = InProcessTransport(CalculatorServer, name="calc")
        client = McpClient(transport, server_name="calc")

        await client.connect()
        try:
            tools = await client.list_tools()
            assert any(t["name"] == "add" for t in tools)

            result = await client.call_tool("add", {"a": -5, "b": 15})
            content = result.get("content", [])
            assert content[0]["text"] == "10"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_close_and_reconnect(self) -> None:
        """Client can close and reconnect to the same server type."""
        transport = InProcessTransport(EchoServer, name="echo")
        client = McpClient(transport, server_name="echo", max_reconnect_attempts=0)

        await client.connect()
        assert client.is_connected
        await client.close()
        assert not client.is_connected

        # Reconnect manually
        await client.connect()
        assert client.is_connected

        result = await client.call_tool("echo", {"text": "reconnected"})
        assert result["content"][0]["text"] == "reconnected"

        await client.close()
