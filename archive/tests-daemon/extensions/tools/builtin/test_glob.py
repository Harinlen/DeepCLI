"""Tests for the Glob tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.glob_tool import GlobTool


@pytest.fixture
def tool() -> GlobTool:
    return GlobTool()


class TestGlobTool:
    """Tests for GlobTool."""

    def test_permission_level(self) -> None:
        assert GlobTool().permission_level == PermissionLevel.NONE

    @pytest.mark.asyncio
    async def test_find_files(self, tool: GlobTool, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")
        (tmp_path / "c.txt").write_text("z")

        ctx = ToolContext(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "*.py"}, ctx)
        assert result.is_error is False
        assert "a.py" in result.output
        assert "b.py" in result.output
        assert "c.txt" not in result.output

    @pytest.mark.asyncio
    async def test_no_matches(self, tool: GlobTool, tmp_path: Path) -> None:
        ctx = ToolContext(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "*.xyz"}, ctx)
        assert "no files" in result.output.lower()

    @pytest.mark.asyncio
    async def test_recursive(self, tool: GlobTool, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("x")

        ctx = ToolContext(cwd=str(tmp_path))
        result = await tool.execute({"pattern": "**/*.py"}, ctx)
        assert "deep.py" in result.output

    @pytest.mark.asyncio
    async def test_explicit_path(self, tool: GlobTool, tmp_path: Path) -> None:
        (tmp_path / "file.rs").write_text("fn main() {}")
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute({"pattern": "*.rs", "path": str(tmp_path)}, ctx)
        assert "file.rs" in result.output

    @pytest.mark.asyncio
    async def test_nonexistent_dir(self, tool: GlobTool) -> None:
        ctx = ToolContext(cwd="/tmp")
        result = await tool.execute({"pattern": "*", "path": "/nonexistent/dir"}, ctx)
        assert result.is_error is True
