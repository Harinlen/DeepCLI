"""Tests for tool base classes."""

from __future__ import annotations

from typing import Any

import pytest

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)


class DummyTool(Tool):
    """Concrete tool for testing the ABC."""

    name = "dummy"
    description = "A dummy tool."
    permission_level = PermissionLevel.NONE

    class Input:
        """Minimal Pydantic-compatible input."""

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {
                "title": "DummyInput",
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
                "required": ["value"],
            }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output=f"got: {params.get('value', '')}")


class TestToolResult:
    """Tests for ToolResult."""

    def test_defaults(self) -> None:
        r = ToolResult(output="ok")
        assert r.output == "ok"
        assert r.is_error is False

    def test_error(self) -> None:
        r = ToolResult(output="fail", is_error=True)
        assert r.is_error is True


class TestToolContext:
    """Tests for ToolContext."""

    def test_creation(self) -> None:
        ctx = ToolContext(cwd="/tmp")
        assert ctx.cwd == "/tmp"


class TestPermissionLevel:
    """Tests for PermissionLevel enum."""

    def test_values(self) -> None:
        assert PermissionLevel.NONE.value == "none"
        assert PermissionLevel.PROMPT.value == "prompt"
        assert PermissionLevel.DANGEROUS.value == "dangerous"


class TestToolABC:
    """Tests for the Tool abstract base class."""

    def test_input_schema_strips_title(self) -> None:
        schema = DummyTool.input_schema()
        assert "title" not in schema
        assert schema["type"] == "object"
        assert "value" in schema["properties"]

    @pytest.mark.asyncio
    async def test_execute(self) -> None:
        tool = DummyTool()
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute({"value": "hello"}, ctx)
        assert result.output == "got: hello"
        assert result.is_error is False


class TestToolInitSubclassValidation:
    """Tests for __init_subclass__ enforcement."""

    def test_missing_name_raises(self) -> None:
        """Tool subclass without name is rejected at class creation."""
        with pytest.raises(TypeError, match="name"):

            class BadTool(Tool):  # type: ignore[type-var]
                description = "has desc"
                permission_level = PermissionLevel.NONE

                class Input:
                    @classmethod
                    def model_json_schema(cls) -> dict[str, Any]:
                        return {"type": "object", "properties": {}}

                async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                    return ToolResult(output="")

    def test_missing_input_raises(self) -> None:
        """Tool subclass without Input class is rejected."""
        with pytest.raises(TypeError, match="Input"):

            class NoInputTool(Tool):  # type: ignore[type-var]
                name = "no_input"
                description = "no input"
                permission_level = PermissionLevel.NONE

                async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                    return ToolResult(output="")

    def test_empty_name_raises(self) -> None:
        """Tool subclass with empty name is rejected."""
        with pytest.raises(TypeError, match="name"):

            class EmptyNameTool(Tool):  # type: ignore[type-var]
                name = ""
                description = "has desc"
                permission_level = PermissionLevel.NONE

                class Input:
                    @classmethod
                    def model_json_schema(cls) -> dict[str, Any]:
                        return {"type": "object", "properties": {}}

                async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                    return ToolResult(output="")
