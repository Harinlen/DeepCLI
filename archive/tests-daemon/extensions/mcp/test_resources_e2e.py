"""End-to-end tests for MCP Resources protocol.

Tests the full chain: McpClient.list_resources / read_resource →
InProcessTransport → test server with resources capability →
McpBridge resource proxy tools + cache.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from daemon.extensions.mcp.bridge import McpBridge, _extract_resource_content
from daemon.extensions.mcp.client import McpClient
from daemon.extensions.mcp.server.protocol import McpServerProtocol
from daemon.extensions.mcp.transport.inprocess import InProcessTransport
from daemon.extensions.tools.registry import ToolRegistry


class ResourceServer(McpServerProtocol):
    """Test MCP server with tools + resources capabilities."""

    RESOURCES = [
        {
            "uri": "file://readme.md",
            "name": "README",
            "description": "Project readme",
            "mimeType": "text/markdown",
        },
        {
            "uri": "db://users/1",
            "name": "User 1",
            "description": "First user record",
        },
    ]

    RESOURCE_CONTENTS: dict[str, dict[str, Any]] = {
        "file://readme.md": {
            "contents": [
                {
                    "uri": "file://readme.md",
                    "mimeType": "text/markdown",
                    "text": "# Hello World\n\nThis is a test.",
                }
            ]
        },
        "db://users/1": {
            "contents": [
                {
                    "uri": "db://users/1",
                    "mimeType": "application/json",
                    "text": '{"id": 1, "name": "Alice"}',
                }
            ]
        },
    }

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
                        "serverInfo": {"name": "resource-server"},
                    },
                }
            case "notifications/initialized":
                return None
            case "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": []},
                }
            case "resources/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"resources": self.RESOURCES},
                }
            case "resources/read":
                uri = request.get("params", {}).get("uri", "")
                content = self.RESOURCE_CONTENTS.get(uri)
                if content:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": content,
                    }
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": f"Resource not found: {uri}",
                    },
                }
            case _:
                return None

    def capabilities(self) -> dict[str, Any]:
        return {"tools": {}, "resources": {}}


class TestClientResources:
    """Tests for McpClient.list_resources / read_resource."""

    @pytest.mark.asyncio
    async def test_list_resources(self) -> None:
        transport = InProcessTransport(ResourceServer, name="res")
        client = McpClient(transport, server_name="res")

        await client.connect()
        try:
            resources = await client.list_resources()
            assert len(resources) == 2
            uris = {r["uri"] for r in resources}
            assert "file://readme.md" in uris
            assert "db://users/1" in uris
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_read_resource(self) -> None:
        transport = InProcessTransport(ResourceServer, name="res")
        client = McpClient(transport, server_name="res")

        await client.connect()
        try:
            result = await client.read_resource("file://readme.md")
            contents = result.get("contents", [])
            assert len(contents) == 1
            assert "Hello World" in contents[0]["text"]
        finally:
            await client.close()


class TestExtractResourceContent:
    """Tests for _extract_resource_content helper."""

    def test_text_content(self) -> None:
        result = {"contents": [{"uri": "a", "text": "hello"}]}
        assert _extract_resource_content(result) == "hello"

    def test_blob_content(self) -> None:
        result = {
            "contents": [
                {
                    "uri": "a",
                    "mimeType": "image/png",
                    "blob": "aGVsbG8=",
                }
            ]
        }
        text = _extract_resource_content(result)
        assert "Binary resource" in text
        assert "aGVsbG8=" in text

    def test_empty_contents(self) -> None:
        assert _extract_resource_content({"contents": []}) == "(empty resource)"

    def test_multiple_contents(self) -> None:
        result = {
            "contents": [
                {"uri": "a", "text": "part1"},
                {"uri": "b", "text": "part2"},
            ]
        }
        text = _extract_resource_content(result)
        assert "part1" in text
        assert "part2" in text


class TestBridgeResourceTools:
    """Tests for McpBridge resource tool registration."""

    @pytest.mark.asyncio
    async def test_registers_resource_tools(self) -> None:
        """Bridge registers list_resources + read_resource tools."""
        transport = InProcessTransport(ResourceServer, name="res")
        client = McpClient(transport, server_name="res")
        registry = ToolRegistry()

        await client.connect()
        try:
            bridge = McpBridge(client, registry)
            names = await bridge.sync_tools()

            assert "mcp__res__list_resources" in names
            assert "mcp__res__read_resource" in names
            assert "mcp__res__list_resources" in registry
            assert "mcp__res__read_resource" in registry
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_no_resource_tools_without_capability(self) -> None:
        """Bridge skips resource tools when server has no resources."""
        from tests.daemon.extensions.mcp.transport.test_inprocess import (
            EchoServer,
        )

        transport = InProcessTransport(EchoServer, name="echo")
        client = McpClient(transport, server_name="echo")
        registry = ToolRegistry()

        await client.connect()
        try:
            bridge = McpBridge(client, registry)
            names = await bridge.sync_tools()

            assert "mcp__echo__list_resources" not in names
            assert "mcp__echo__read_resource" not in names
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_resources_tool_execution(self) -> None:
        """Execute the list_resources proxy tool."""
        transport = InProcessTransport(ResourceServer, name="res")
        client = McpClient(transport, server_name="res")
        registry = ToolRegistry()

        await client.connect()
        try:
            bridge = McpBridge(client, registry)
            await bridge.sync_tools()

            tool = registry.get("mcp__res__list_resources")
            assert tool is not None

            from daemon.extensions.tools.base import ToolContext

            ctx = ToolContext(cwd="/tmp")
            result = await tool.execute({}, ctx)
            assert not result.is_error
            parsed = json.loads(result.output)
            assert len(parsed) == 2
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_read_resource_tool_execution(self) -> None:
        """Execute the read_resource proxy tool with caching."""
        transport = InProcessTransport(ResourceServer, name="res")
        client = McpClient(transport, server_name="res")
        registry = ToolRegistry()

        await client.connect()
        try:
            bridge = McpBridge(client, registry)
            await bridge.sync_tools()

            tool = registry.get("mcp__res__read_resource")
            assert tool is not None

            from daemon.extensions.tools.base import ToolContext

            ctx = ToolContext(cwd="/tmp")

            # First call — fetches from server
            result = await tool.execute({"uri": "file://readme.md"}, ctx)
            assert not result.is_error
            assert "Hello World" in result.output

            # Second call — should hit cache (same result)
            result2 = await tool.execute({"uri": "file://readme.md"}, ctx)
            assert result2.output == result.output

            # Verify cache has the entry
            assert len(bridge._resource_cache) == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_read_resource_missing_uri(self) -> None:
        """read_resource with no URI returns error."""
        transport = InProcessTransport(ResourceServer, name="res")
        client = McpClient(transport, server_name="res")
        registry = ToolRegistry()

        await client.connect()
        try:
            bridge = McpBridge(client, registry)
            await bridge.sync_tools()

            tool = registry.get("mcp__res__read_resource")
            from daemon.extensions.tools.base import ToolContext

            ctx = ToolContext(cwd="/tmp")
            result = await tool.execute({}, ctx)
            assert result.is_error
            assert "Missing" in result.output
        finally:
            await client.close()
