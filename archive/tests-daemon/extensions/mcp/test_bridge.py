"""Tests for MCP tool bridge."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.extensions.mcp.bridge import (
    McpBridge,
    McpProxyTool,
    _extract_text_content,
    build_mcp_tool_name,
    normalize_mcp_name,
)
from daemon.extensions.tools.base import PermissionLevel
from daemon.extensions.tools.registry import ToolRegistry


class TestNormalization:
    """Tests for MCP name normalization."""

    def test_simple_name(self) -> None:
        assert normalize_mcp_name("filesystem") == "filesystem"

    def test_name_with_special_chars(self) -> None:
        assert normalize_mcp_name("my.server@v2") == "my_server_v2"

    def test_name_with_hyphens(self) -> None:
        """Hyphens are preserved."""
        assert normalize_mcp_name("my-server") == "my-server"

    def test_name_with_spaces(self) -> None:
        assert normalize_mcp_name("my server") == "my_server"

    def test_build_tool_name(self) -> None:
        assert build_mcp_tool_name("fs", "read_file") == "mcp__fs__read_file"

    def test_build_tool_name_special(self) -> None:
        assert build_mcp_tool_name("my.server", "do stuff") == "mcp__my_server__do_stuff"


class TestExtractTextContent:
    """Tests for _extract_text_content()."""

    def test_text_blocks(self) -> None:
        result = {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ]
        }
        assert _extract_text_content(result) == "hello\nworld"

    def test_image_block(self) -> None:
        result = {"content": [{"type": "image", "mimeType": "image/png"}]}
        assert "[Image: image/png]" in _extract_text_content(result)

    def test_resource_block(self) -> None:
        result = {"content": [{"type": "resource", "resource": {"uri": "file:///tmp/test"}}]}
        assert "[Resource: file:///tmp/test]" in _extract_text_content(result)

    def test_empty_content(self) -> None:
        result = {"content": []}
        assert _extract_text_content(result) == "(empty result)"

    def test_no_content_key(self) -> None:
        result = {"text": "fallback"}
        assert _extract_text_content(result) == "fallback"

    def test_unknown_block_type(self) -> None:
        result = {"content": [{"type": "video"}]}
        assert "[video]" in _extract_text_content(result)


class TestMcpProxyTool:
    """Tests for McpProxyTool."""

    def _make_proxy(self, tool_def: dict[str, Any] | None = None) -> McpProxyTool:
        """Create a proxy tool with mock client."""
        client = MagicMock()
        client.server_name = "test-srv"
        client.call_tool = AsyncMock()

        if tool_def is None:
            tool_def = {
                "name": "read_file",
                "description": "Read a file from disk",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        return McpProxyTool(client, "test-srv", tool_def)

    def test_name_normalization(self) -> None:
        proxy = self._make_proxy()
        assert proxy.name == "mcp__test-srv__read_file"

    def test_description(self) -> None:
        proxy = self._make_proxy()
        assert proxy.description == "Read a file from disk"

    def test_permission_level(self) -> None:
        proxy = self._make_proxy()
        assert proxy.permission_level == PermissionLevel.PROMPT

    def test_input_schema(self) -> None:
        proxy = self._make_proxy()
        schema = proxy._input_schema_instance()
        assert "properties" in schema
        assert "path" in schema["properties"]

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        proxy = self._make_proxy()
        proxy._client.call_tool.return_value = {
            "content": [{"type": "text", "text": "file contents"}]
        }

        from daemon.extensions.tools.base import ToolContext

        result = await proxy.execute({"path": "/tmp/test"}, ToolContext(cwd="."))

        assert result.output == "file contents"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_execute_error(self) -> None:
        proxy = self._make_proxy()
        proxy._client.call_tool.return_value = {
            "isError": True,
            "content": [{"type": "text", "text": "not found"}],
        }

        from daemon.extensions.tools.base import ToolContext

        result = await proxy.execute({"path": "/bad"}, ToolContext(cwd="."))

        assert result.is_error is True
        assert "not found" in result.output

    @pytest.mark.asyncio
    async def test_execute_transport_error(self) -> None:
        proxy = self._make_proxy()
        proxy._client.call_tool.side_effect = Exception("connection lost")

        from daemon.extensions.tools.base import ToolContext

        result = await proxy.execute({"path": "/tmp"}, ToolContext(cwd="."))

        assert result.is_error is True
        assert "MCP tool error" in result.output

    @pytest.mark.asyncio
    async def test_execute_returns_raw_output(self) -> None:
        """MCP proxy returns raw output — budget enforced by orchestrator."""
        proxy = self._make_proxy()
        large_text = "x" * 100_000
        proxy._client.call_tool.return_value = {"content": [{"type": "text", "text": large_text}]}

        from daemon.extensions.tools.base import ToolContext

        result = await proxy.execute({}, ToolContext(cwd="."))

        # Raw output passed through — orchestrator handles truncation
        assert result.output == large_text
        assert result.is_error is False

    def test_description_truncation(self) -> None:
        """Long descriptions are truncated."""
        tool_def = {
            "name": "verbose_tool",
            "description": "A" * 2000,
            "inputSchema": {},
        }
        proxy = self._make_proxy(tool_def)
        assert len(proxy.description) == 1024


class TestMcpBridge:
    """Tests for McpBridge."""

    @pytest.mark.asyncio
    async def test_sync_tools_registers(self) -> None:
        """sync_tools creates proxy tools and registers them."""
        client = MagicMock()
        client.server_name = "bridge-srv"
        client.server_capabilities = {}
        client.list_tools = AsyncMock(
            return_value=[
                {"name": "tool_a", "description": "A", "inputSchema": {}},
                {"name": "tool_b", "description": "B", "inputSchema": {}},
            ]
        )

        registry = ToolRegistry()
        bridge = McpBridge(client, registry)

        names = await bridge.sync_tools()

        assert len(names) == 2
        assert "mcp__bridge-srv__tool_a" in registry
        assert "mcp__bridge-srv__tool_b" in registry
        assert bridge.get_tool_names() == names

    @pytest.mark.asyncio
    async def test_sync_tools_unregisters_old(self) -> None:
        """sync_tools removes old tools before registering new ones."""
        client = MagicMock()
        client.server_name = "srv"
        client.server_capabilities = {}
        client.list_tools = AsyncMock(
            side_effect=[
                [{"name": "old_tool", "description": "O", "inputSchema": {}}],
                [{"name": "new_tool", "description": "N", "inputSchema": {}}],
            ]
        )

        registry = ToolRegistry()
        bridge = McpBridge(client, registry)

        # First sync
        await bridge.sync_tools()
        assert "mcp__srv__old_tool" in registry

        # Second sync — old tool removed, new one added
        await bridge.sync_tools()
        assert "mcp__srv__old_tool" not in registry
        assert "mcp__srv__new_tool" in registry

    @pytest.mark.asyncio
    async def test_sync_tools_conflict_skipped(self) -> None:
        """Tools conflicting with existing names are skipped."""
        client = MagicMock()
        client.server_name = "srv"
        client.server_capabilities = {}
        client.list_tools = AsyncMock(
            return_value=[
                {"name": "my_tool", "description": "T", "inputSchema": {}},
            ]
        )

        registry = ToolRegistry()

        # Pre-register a conflicting tool
        from daemon.extensions.tools.base import Tool, PermissionLevel, ToolContext, ToolResult
        from pydantic import BaseModel

        class ExistingTool(Tool):
            name = "mcp__srv__my_tool"
            description = "Already exists"
            permission_level = PermissionLevel.NONE

            class Input(BaseModel):
                pass

            async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
                return ToolResult(output="")

        registry.register(ExistingTool())

        bridge = McpBridge(client, registry)
        names = await bridge.sync_tools()

        assert names == []  # Skipped due to conflict

    @pytest.mark.asyncio
    async def test_sync_tools_missing_name_skipped(self) -> None:
        """Tool defs without 'name' are skipped."""
        client = MagicMock()
        client.server_name = "srv"
        client.server_capabilities = {}
        client.list_tools = AsyncMock(
            return_value=[
                {"description": "No name", "inputSchema": {}},
            ]
        )

        registry = ToolRegistry()
        bridge = McpBridge(client, registry)

        names = await bridge.sync_tools()
        assert names == []
