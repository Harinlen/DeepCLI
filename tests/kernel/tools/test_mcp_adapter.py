"""Tests for kernel.tools.mcp_adapter — MCPAdapter + naming utils."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from kernel.mcp.types import McpToolDef, McpToolResult
from kernel.tools.mcp_adapter import (
    MCPAdapter,
    build_mcp_tool_name,
    extract_text_content,
)


class TestBuildMcpToolName:
    """build_mcp_tool_name() normalization tests."""

    def test_simple(self) -> None:
        assert build_mcp_tool_name("github", "list_issues") == "mcp__github__list_issues"

    def test_special_chars(self) -> None:
        assert build_mcp_tool_name("my server", "do.thing") == "mcp__my_server__do_thing"

    def test_hyphens_preserved(self) -> None:
        assert build_mcp_tool_name("my-server", "my-tool") == "mcp__my-server__my-tool"

    def test_underscores_preserved(self) -> None:
        assert build_mcp_tool_name("a_b", "c_d") == "mcp__a_b__c_d"


class TestExtractTextContent:
    """extract_text_content() tests."""

    def test_text_blocks(self) -> None:
        blocks = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        assert extract_text_content(blocks) == "hello\nworld"

    def test_image_placeholder(self) -> None:
        blocks = [{"type": "image", "data": "..."}]
        assert extract_text_content(blocks) == "[image]"

    def test_resource_placeholder(self) -> None:
        blocks = [{"type": "resource", "resource": {"uri": "file:///x.txt"}}]
        assert extract_text_content(blocks) == "[resource: file:///x.txt]"

    def test_unknown_type(self) -> None:
        blocks = [{"type": "audio"}]
        assert extract_text_content(blocks) == "[audio]"

    def test_empty(self) -> None:
        assert extract_text_content([]) == ""


class TestMCPAdapter:
    """MCPAdapter tool interface tests."""

    def _make_adapter(self, mcp_mock: Any = None) -> MCPAdapter:
        tool_def = McpToolDef(
            name="echo",
            description="Echo the input back",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        )
        return MCPAdapter(
            server_name="test-server",
            tool_def=tool_def,
            mcp_manager=mcp_mock or AsyncMock(),
        )

    def test_name(self) -> None:
        adapter = self._make_adapter()
        assert adapter.name == "mcp__test-server__echo"

    def test_description_truncated(self) -> None:
        tool_def = McpToolDef(name="x", description="a" * 3000)
        adapter = MCPAdapter("s", tool_def, AsyncMock())
        assert len(adapter.description) == 2048

    def test_to_schema(self) -> None:
        adapter = self._make_adapter()
        schema = adapter.to_schema()
        assert schema.name == "mcp__test-server__echo"
        assert "Echo" in schema.description
        assert schema.input_schema["type"] == "object"

    def test_user_facing_name(self) -> None:
        adapter = self._make_adapter()
        assert adapter.user_facing_name({}) == "test-server/echo"

    def test_default_risk_is_ask(self) -> None:
        adapter = self._make_adapter()
        risk = adapter.default_risk({}, None)  # type: ignore[arg-type]
        assert risk.default_decision == "ask"
        assert risk.risk == "medium"

    @pytest.mark.anyio
    async def test_call_delegates_to_mcp(self) -> None:
        mcp_mock = AsyncMock()
        mcp_mock.call_tool.return_value = McpToolResult(
            content=[{"type": "text", "text": "echo: hi"}],
        )
        adapter = self._make_adapter(mcp_mock)

        results = []
        async for event in adapter.call({"msg": "hi"}, None):  # type: ignore[arg-type]
            results.append(event)

        assert len(results) == 1
        mcp_mock.call_tool.assert_awaited_once_with("test-server", "echo", {"msg": "hi"})
        assert results[0].llm_content[0].text == "echo: hi"
