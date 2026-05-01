"""Tests for the tool registry."""

from __future__ import annotations

from typing import Any

import pytest

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.registry import ToolRegistry


class FakeTool(Tool):
    """Test tool."""

    name = "fake"
    description = "Fake."
    permission_level = PermissionLevel.NONE

    class Input:
        """Minimal input."""

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {"x": {"type": "string"}}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="ok")


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_register_and_get(self) -> None:
        reg = ToolRegistry()
        tool = FakeTool()
        reg.register(tool)
        assert reg.get("fake") is tool
        assert reg.get("nonexistent") is None

    def test_duplicate_raises(self) -> None:
        reg = ToolRegistry()
        reg.register(FakeTool())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(FakeTool())

    def test_get_definitions(self) -> None:
        reg = ToolRegistry()
        reg.register(FakeTool())
        defs = reg.get_definitions()
        assert len(defs) == 1
        assert defs[0].name == "fake"
        assert defs[0].description == "Fake."
        assert "x" in defs[0].parameters["properties"]

    def test_tool_names(self) -> None:
        reg = ToolRegistry()
        reg.register(FakeTool())
        assert reg.tool_names == ["fake"]

    def test_len_and_contains(self) -> None:
        reg = ToolRegistry()
        assert len(reg) == 0
        assert "fake" not in reg
        reg.register(FakeTool())
        assert len(reg) == 1
        assert "fake" in reg

    def test_unregister(self) -> None:
        """unregister removes a tool by name."""
        reg = ToolRegistry()
        reg.register(FakeTool())
        assert "fake" in reg

        assert reg.unregister("fake") is True
        assert "fake" not in reg
        assert len(reg) == 0

    def test_unregister_missing(self) -> None:
        """unregister of nonexistent name returns False."""
        reg = ToolRegistry()
        assert reg.unregister("nonexistent") is False
