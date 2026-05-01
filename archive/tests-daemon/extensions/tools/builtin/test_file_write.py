"""Tests for the FileWrite tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.file_write import FileWriteTool


@pytest.fixture
def tool() -> FileWriteTool:
    return FileWriteTool()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


class TestFileWriteTool:
    """Tests for FileWriteTool."""

    def test_permission_level(self) -> None:
        assert FileWriteTool().permission_level == PermissionLevel.PROMPT

    @pytest.mark.asyncio
    async def test_write_new_file(
        self, tool: FileWriteTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        f = tmp_path / "new.txt"
        result = await tool.execute({"file_path": str(f), "content": "hello\nworld\n"}, ctx)
        assert result.is_error is False
        assert f.read_text() == "hello\nworld\n"
        assert "2 lines" in result.output

    @pytest.mark.asyncio
    async def test_creates_parent_dirs(
        self, tool: FileWriteTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        f = tmp_path / "a" / "b" / "c.txt"
        result = await tool.execute({"file_path": str(f), "content": "nested"}, ctx)
        assert result.is_error is False
        assert f.read_text() == "nested"

    @pytest.mark.asyncio
    async def test_overwrites_existing(
        self, tool: FileWriteTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        f = tmp_path / "existing.txt"
        f.write_text("old content")
        result = await tool.execute({"file_path": str(f), "content": "new content"}, ctx)
        assert result.is_error is False
        assert f.read_text() == "new content"

    @pytest.mark.asyncio
    async def test_relative_path_rejected(self, tool: FileWriteTool, ctx: ToolContext) -> None:
        result = await tool.execute({"file_path": "relative.txt", "content": "x"}, ctx)
        assert result.is_error is True
        assert "absolute" in result.output.lower()
