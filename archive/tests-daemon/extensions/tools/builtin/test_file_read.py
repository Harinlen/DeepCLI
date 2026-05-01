"""Tests for the FileRead tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.file_read import FileReadTool


@pytest.fixture
def tool() -> FileReadTool:
    return FileReadTool()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


class TestFileReadTool:
    """Tests for FileReadTool."""

    def test_permission_level(self) -> None:
        assert FileReadTool().permission_level == PermissionLevel.NONE

    @pytest.mark.asyncio
    async def test_read_file(self, tool: FileReadTool, ctx: ToolContext, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("line one\nline two\nline three\n")
        result = await tool.execute({"file_path": str(f)}, ctx)
        assert result.is_error is False
        assert "1\tline one" in result.output
        assert "2\tline two" in result.output

    @pytest.mark.asyncio
    async def test_file_not_found(self, tool: FileReadTool, ctx: ToolContext) -> None:
        result = await tool.execute({"file_path": "/nonexistent/path.txt"}, ctx)
        assert result.is_error is True
        assert "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_relative_path_rejected(self, tool: FileReadTool, ctx: ToolContext) -> None:
        result = await tool.execute({"file_path": "relative.txt"}, ctx)
        assert result.is_error is True
        assert "absolute" in result.output.lower()

    @pytest.mark.asyncio
    async def test_binary_file_detected(
        self, tool: FileReadTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        result = await tool.execute({"file_path": str(f)}, ctx)
        assert result.is_error is True
        assert "binary" in result.output.lower()

    @pytest.mark.asyncio
    async def test_offset_and_limit(
        self, tool: FileReadTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        f = tmp_path / "lines.txt"
        f.write_text("\n".join(f"line {i}" for i in range(20)))
        result = await tool.execute({"file_path": str(f), "offset": 5, "limit": 3}, ctx)
        assert result.is_error is False
        # Should start at line 6 (1-indexed)
        assert "6\tline 5" in result.output
        assert "8\tline 7" in result.output

    @pytest.mark.asyncio
    async def test_empty_file(self, tool: FileReadTool, ctx: ToolContext, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = await tool.execute({"file_path": str(f)}, ctx)
        assert result.is_error is False
        assert "empty" in result.output.lower()

    @pytest.mark.asyncio
    async def test_truncation_notice(
        self, tool: FileReadTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"line {i}" for i in range(100)))
        result = await tool.execute({"file_path": str(f), "limit": 10}, ctx)
        assert "more lines" in result.output

    @pytest.mark.asyncio
    async def test_image_file_returns_image_parts(
        self, tool: FileReadTool, ctx: ToolContext, tmp_path: Path
    ) -> None:
        """Step 5.6: image files return base64 via image_parts, not text body."""
        from PIL import Image

        img_path = tmp_path / "pic.png"
        Image.new("RGB", (32, 32), (10, 200, 30)).save(img_path, "PNG")

        result = await tool.execute({"file_path": str(img_path)}, ctx)
        assert result.is_error is False
        assert result.image_parts is not None
        assert len(result.image_parts) == 1
        assert result.image_parts[0].media_type == "image/png"
        assert result.image_parts[0].data_base64
        assert "image" in result.output.lower()
