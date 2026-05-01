"""Tests for the Grep tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.grep_tool import GrepTool


@pytest.fixture
def tool() -> GrepTool:
    return GrepTool()


class TestGrepTool:
    """Tests for GrepTool."""

    def test_permission_level(self) -> None:
        assert GrepTool().permission_level == PermissionLevel.NONE

    @pytest.mark.asyncio
    async def test_search_content(self, tool: GrepTool, tmp_path: Path) -> None:
        (tmp_path / "hello.txt").write_text("hello world\nfoo bar\nhello again\n")
        ctx = ToolContext(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "hello", "path": str(tmp_path)}, ctx)
        assert result.is_error is False
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_no_matches(self, tool: GrepTool, tmp_path: Path) -> None:
        (tmp_path / "test.txt").write_text("abc def")
        ctx = ToolContext(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "xyz123", "path": str(tmp_path)}, ctx)
        assert "no matches" in result.output.lower()

    @pytest.mark.asyncio
    async def test_files_with_matches_mode(self, tool: GrepTool, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("target\n")
        (tmp_path / "b.txt").write_text("other\n")
        ctx = ToolContext(cwd=str(tmp_path))
        result = await tool.execute(
            {
                "pattern": "target",
                "path": str(tmp_path),
                "output_mode": "files_with_matches",
            },
            ctx,
        )
        assert "a.txt" in result.output
        assert "b.txt" not in result.output

    @pytest.mark.asyncio
    async def test_nonexistent_path(self, tool: GrepTool) -> None:
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute({"pattern": "x", "path": "/nonexistent/path"}, ctx)
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_default_uses_cwd(self, tool: GrepTool, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("findme\n")
        ctx = ToolContext(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "findme"}, ctx)
        assert "findme" in result.output
