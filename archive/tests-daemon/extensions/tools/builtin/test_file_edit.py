"""Tests for the FileEdit tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.file_edit import FileEditTool


@pytest.fixture
def tool() -> FileEditTool:
    return FileEditTool()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


class TestFileEditTool:
    """Tests for FileEditTool."""

    def test_permission_level(self) -> None:
        assert FileEditTool().permission_level == PermissionLevel.PROMPT

    @pytest.mark.asyncio
    async def test_replace_unique(
        self, tool: FileEditTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = await tool.execute(
            {"file_path": str(f), "old_string": "hello", "new_string": "goodbye"}, ctx
        )
        assert result.is_error is False
        assert f.read_text() == "goodbye world"
        assert "1 occurrence" in result.output

    @pytest.mark.asyncio
    async def test_not_found(self, tool: FileEditTool, ctx: ToolContext, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = await tool.execute(
            {"file_path": str(f), "old_string": "xyz", "new_string": "abc"}, ctx
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_not_unique(self, tool: FileEditTool, ctx: ToolContext, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("aaa bbb aaa")
        result = await tool.execute(
            {"file_path": str(f), "old_string": "aaa", "new_string": "ccc"}, ctx
        )
        assert result.is_error is True
        assert "2 times" in result.output

    @pytest.mark.asyncio
    async def test_replace_all(self, tool: FileEditTool, ctx: ToolContext, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("aaa bbb aaa")
        result = await tool.execute(
            {
                "file_path": str(f),
                "old_string": "aaa",
                "new_string": "ccc",
                "replace_all": True,
            },
            ctx,
        )
        assert result.is_error is False
        assert f.read_text() == "ccc bbb ccc"
        assert "2 occurrence" in result.output

    @pytest.mark.asyncio
    async def test_same_string_rejected(
        self, tool: FileEditTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = await tool.execute(
            {"file_path": str(f), "old_string": "hello", "new_string": "hello"}, ctx
        )
        assert result.is_error is True
        assert "identical" in result.output.lower()

    @pytest.mark.asyncio
    async def test_file_not_found(self, tool: FileEditTool, ctx: ToolContext) -> None:
        result = await tool.execute(
            {
                "file_path": "/nonexistent/test.txt",
                "old_string": "a",
                "new_string": "b",
            },
            ctx,
        )
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_relative_path_rejected(self, tool: FileEditTool, ctx: ToolContext) -> None:
        result = await tool.execute(
            {"file_path": "relative.txt", "old_string": "a", "new_string": "b"}, ctx
        )
        assert result.is_error is True
        assert "absolute" in result.output.lower()

    @pytest.mark.asyncio
    async def test_binary_file_rejected(
        self, tool: FileEditTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        """Editing a binary file returns an error instead of crashing."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"hello\x00world")
        result = await tool.execute(
            {"file_path": str(f), "old_string": "hello", "new_string": "bye"}, ctx
        )
        assert result.is_error is True
        assert "binary" in result.output.lower()

    @pytest.mark.asyncio
    async def test_empty_old_string_rejected(
        self, tool: FileEditTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        """Empty old_string is rejected by Pydantic min_length validation."""
        from pydantic import ValidationError

        f = tmp_path / "test.txt"
        f.write_text("hello")
        with pytest.raises(ValidationError, match="old_string"):
            await tool.execute({"file_path": str(f), "old_string": "", "new_string": "x"}, ctx)
