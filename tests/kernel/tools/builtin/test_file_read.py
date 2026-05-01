"""Tests for FileReadTool — text, image, and PDF reading."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kernel.llm.types import ImageContent, TextContent
from kernel.tools.builtin.file_read import (
    FileReadTool,
    _format_page_list,
    _parse_pages,
)
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileStateCache
from kernel.tools.types import FileDisplay, ToolCallResult, ToolInputError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RiskCtx:
    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def session_id(self) -> str:
        return "test-session"


def _make_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="test-session",
        agent_depth=0,
        agent_id=None,
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        file_state=FileStateCache(),
    )


async def _run(tool: FileReadTool, input: dict[str, Any], ctx: ToolContext) -> ToolCallResult:
    results = []
    async for event in tool.call(input, ctx):
        results.append(event)
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


class TestValidateInput:
    tool = FileReadTool()

    async def test_missing_path(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="path"):
            await self.tool.validate_input({}, _RiskCtx(tmp_path))

    async def test_empty_path(self, tmp_path: Path) -> None:
        with pytest.raises(ToolInputError, match="path"):
            await self.tool.validate_input({"path": ""}, _RiskCtx(tmp_path))

    async def test_valid(self, tmp_path: Path) -> None:
        await self.tool.validate_input({"path": "foo.txt"}, _RiskCtx(tmp_path))


# ---------------------------------------------------------------------------
# default_risk
# ---------------------------------------------------------------------------


class TestDefaultRisk:
    tool = FileReadTool()

    def test_always_low_risk(self, tmp_path: Path) -> None:
        result = self.tool.default_risk({"path": "/etc/passwd"}, _RiskCtx(tmp_path))
        assert result.risk == "low"
        assert result.default_decision == "allow"


# ---------------------------------------------------------------------------
# call() — text files
# ---------------------------------------------------------------------------


class TestCallText:
    tool = FileReadTool()

    async def test_file_not_found(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(tmp_path / "nope.txt")}, ctx)
        assert "not found" in result.data["error"]

    async def test_not_a_file(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(tmp_path)}, ctx)
        assert "not a regular file" in result.data["error"]

    async def test_read_full_file(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("line1\nline2\nline3\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f)}, ctx)
        assert result.data["total_lines"] == 3
        assert result.data["start_line"] == 1
        assert result.data["end_line"] == 3
        assert not result.data["truncated"]

    async def test_line_range(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        lines = [f"line{i}\n" for i in range(1, 11)]
        f.write_text("".join(lines))
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "start_line": 3, "limit": 2}, ctx)
        assert result.data["start_line"] == 3
        assert result.data["end_line"] == 4
        assert result.data["total_lines"] == 10
        assert "line3" in result.data["content"]
        assert "line4" in result.data["content"]

    async def test_records_file_state(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("content")
        ctx = _make_ctx(tmp_path)
        await _run(self.tool, {"path": str(f)}, ctx)
        state = ctx.file_state.verify(f)
        assert state is not None

    async def test_relative_path(self, tmp_path: Path) -> None:
        f = tmp_path / "sub" / "f.txt"
        f.parent.mkdir()
        f.write_text("hello")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": "sub/f.txt"}, ctx)
        assert "hello" in result.data["content"]

    async def test_display_type(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("hello")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f)}, ctx)
        assert isinstance(result.display, FileDisplay)

    async def test_truncation_by_lines(self, tmp_path: Path) -> None:
        """Reading fewer lines than total marks truncated=True."""
        f = tmp_path / "f.txt"
        lines = [f"line{i}\n" for i in range(1, 20)]
        f.write_text("".join(lines))
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "limit": 5}, ctx)
        assert result.data["truncated"] is True
        assert result.data["end_line"] == 5

    async def test_start_line_clamps_to_1(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("line1\n")
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(f), "start_line": -5}, ctx)
        assert result.data["start_line"] == 1


# ---------------------------------------------------------------------------
# call() — image files
# ---------------------------------------------------------------------------


class TestCallImage:
    tool = FileReadTool()

    async def test_read_png(self, tmp_path: Path) -> None:
        """A PNG file returns ImageContent with correct MIME type."""
        img = tmp_path / "test.png"
        pixel_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        img.write_bytes(pixel_data)
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(img)}, ctx)

        assert result.data["type"] == "image"
        assert result.data["size_bytes"] == len(pixel_data)
        assert len(result.llm_content) == 1
        block = result.llm_content[0]
        assert isinstance(block, ImageContent)
        assert block.media_type == "image/png"
        # Verify base64 round-trip.
        assert base64.b64decode(block.data_base64) == pixel_data

    async def test_read_jpeg(self, tmp_path: Path) -> None:
        """A JPEG file returns ImageContent with image/jpeg MIME."""
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(img)}, ctx)

        assert result.data["type"] == "image"
        block = result.llm_content[0]
        assert isinstance(block, ImageContent)
        assert block.media_type == "image/jpeg"

    async def test_read_webp(self, tmp_path: Path) -> None:
        img = tmp_path / "test.webp"
        img.write_bytes(b"RIFF" + b"\x00" * 50)
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(img)}, ctx)

        block = result.llm_content[0]
        assert isinstance(block, ImageContent)
        assert block.media_type == "image/webp"

    async def test_read_gif(self, tmp_path: Path) -> None:
        img = tmp_path / "anim.gif"
        img.write_bytes(b"GIF89a" + b"\x00" * 50)
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(img)}, ctx)

        block = result.llm_content[0]
        assert isinstance(block, ImageContent)
        assert block.media_type == "image/gif"

    async def test_image_too_large(self, tmp_path: Path) -> None:
        """Images exceeding the size limit produce an error, not a crash."""
        img = tmp_path / "huge.png"
        img.write_bytes(b"\x00" * (20 * 1024 * 1024 + 1))
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(img)}, ctx)

        assert "error" in result.data
        assert "too large" in result.data["error"]

    async def test_image_display_is_file_display(self, tmp_path: Path) -> None:
        img = tmp_path / "x.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 10)
        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(img)}, ctx)

        assert isinstance(result.display, FileDisplay)
        assert "[image:" in result.display.content

    async def test_image_does_not_record_file_state(self, tmp_path: Path) -> None:
        """Images should not participate in file_state tracking."""
        img = tmp_path / "x.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 10)
        ctx = _make_ctx(tmp_path)
        await _run(self.tool, {"path": str(img)}, ctx)

        assert ctx.file_state.verify(img) is None


# ---------------------------------------------------------------------------
# call() — PDF files
# ---------------------------------------------------------------------------


class TestCallPdf:
    tool = FileReadTool()

    async def test_pdf_missing_pymupdf(self, tmp_path: Path) -> None:
        """When pymupdf is not installed, a clear error is returned."""
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        ctx = _make_ctx(tmp_path)

        with patch.dict("sys.modules", {"fitz": None}):
            result = await _run(self.tool, {"path": str(pdf)}, ctx)

        assert "error" in result.data
        assert "pymupdf" in result.data["error"]

    async def test_pdf_small_auto_pages(self, tmp_path: Path) -> None:
        """A small PDF (<= 10 pages) auto-renders all pages."""
        fitz = pytest.importorskip("fitz")

        pdf = tmp_path / "small.pdf"
        doc = fitz.open()
        for _ in range(3):
            doc.new_page(width=100, height=100)
        doc.save(str(pdf))
        doc.close()

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf)}, ctx)

        assert result.data["type"] == "pdf"
        assert result.data["total_pages"] == 3
        assert result.data["rendered_pages"] == [1, 2, 3]
        # 1 TextContent header + 3 ImageContent pages.
        assert len(result.llm_content) == 4
        assert isinstance(result.llm_content[0], TextContent)
        for block in result.llm_content[1:]:
            assert isinstance(block, ImageContent)
            assert block.media_type == "image/png"

    async def test_pdf_large_requires_pages(self, tmp_path: Path) -> None:
        """A PDF with > 10 pages requires explicit pages parameter."""
        fitz = pytest.importorskip("fitz")

        pdf = tmp_path / "large.pdf"
        doc = fitz.open()
        for _ in range(15):
            doc.new_page(width=100, height=100)
        doc.save(str(pdf))
        doc.close()

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf)}, ctx)

        assert "error" in result.data
        assert "15 pages" in result.data["error"]

    async def test_pdf_explicit_page_range(self, tmp_path: Path) -> None:
        """Explicit page range renders only the requested pages."""
        fitz = pytest.importorskip("fitz")

        pdf = tmp_path / "doc.pdf"
        doc = fitz.open()
        for _ in range(15):
            doc.new_page(width=100, height=100)
        doc.save(str(pdf))
        doc.close()

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf), "pages": "3-5"}, ctx)

        assert result.data["type"] == "pdf"
        assert result.data["total_pages"] == 15
        assert result.data["rendered_pages"] == [3, 4, 5]
        # 1 header + 3 pages.
        assert len(result.llm_content) == 4

    async def test_pdf_single_page(self, tmp_path: Path) -> None:
        """Requesting a single page works correctly."""
        fitz = pytest.importorskip("fitz")

        pdf = tmp_path / "doc.pdf"
        doc = fitz.open()
        for _ in range(5):
            doc.new_page(width=100, height=100)
        doc.save(str(pdf))
        doc.close()

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf), "pages": "2"}, ctx)

        assert result.data["rendered_pages"] == [2]
        assert len(result.llm_content) == 2  # 1 header + 1 page

    async def test_pdf_display_is_file_display(self, tmp_path: Path) -> None:
        fitz = pytest.importorskip("fitz")

        pdf = tmp_path / "doc.pdf"
        doc = fitz.open()
        doc.new_page(width=100, height=100)
        doc.save(str(pdf))
        doc.close()

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf)}, ctx)

        assert isinstance(result.display, FileDisplay)
        assert "[PDF:" in result.display.content

    async def test_pdf_empty(self, tmp_path: Path) -> None:
        """An empty PDF (no pages) returns an error."""
        pytest.importorskip("fitz")

        # Write a minimal valid PDF with zero pages.  PyMuPDF's save()
        # rejects zero-page documents, so we write raw bytes instead.
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(
            b"%PDF-1.0\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
            b"xref\n0 3\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"trailer<</Size 3/Root 1 0 R>>\nstartxref\n109\n%%EOF"
        )

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf)}, ctx)

        assert "error" in result.data
        assert "no pages" in result.data["error"]

    async def test_pdf_invalid_page_range(self, tmp_path: Path) -> None:
        """An invalid page range returns an error, not a crash."""
        fitz = pytest.importorskip("fitz")

        pdf = tmp_path / "doc.pdf"
        doc = fitz.open()
        doc.new_page(width=100, height=100)
        doc.save(str(pdf))
        doc.close()

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf), "pages": "abc"}, ctx)

        assert "error" in result.data

    async def test_pdf_page_out_of_range_clamps(self, tmp_path: Path) -> None:
        """Pages beyond total are silently skipped; valid pages render."""
        fitz = pytest.importorskip("fitz")

        pdf = tmp_path / "doc.pdf"
        doc = fitz.open()
        for _ in range(3):
            doc.new_page(width=100, height=100)
        doc.save(str(pdf))
        doc.close()

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf), "pages": "2-100"}, ctx)

        # Pages 2 and 3 should render; 4-100 are silently skipped.
        assert result.data["rendered_pages"] == [2, 3]

    async def test_pdf_comma_separated(self, tmp_path: Path) -> None:
        """Comma-separated page specs work correctly."""
        fitz = pytest.importorskip("fitz")

        pdf = tmp_path / "doc.pdf"
        doc = fitz.open()
        for _ in range(10):
            doc.new_page(width=100, height=100)
        doc.save(str(pdf))
        doc.close()

        ctx = _make_ctx(tmp_path)
        result = await _run(self.tool, {"path": str(pdf), "pages": "1,3,5"}, ctx)

        assert result.data["rendered_pages"] == [1, 3, 5]


# ---------------------------------------------------------------------------
# _parse_pages — unit tests for the page range parser
# ---------------------------------------------------------------------------


class TestParsePages:
    def test_auto_small(self) -> None:
        assert _parse_pages(None, 5) == [0, 1, 2, 3, 4]

    def test_auto_large_raises(self) -> None:
        with pytest.raises(ValueError, match="15 pages"):
            _parse_pages(None, 15)

    def test_single_page(self) -> None:
        assert _parse_pages("3", 10) == [2]

    def test_range(self) -> None:
        assert _parse_pages("2-4", 10) == [1, 2, 3]

    def test_comma_separated(self) -> None:
        assert _parse_pages("1,5,10", 10) == [0, 4, 9]

    def test_mixed(self) -> None:
        assert _parse_pages("1-3,7,10-12", 15) == [0, 1, 2, 6, 9, 10, 11]

    def test_clamp_to_total(self) -> None:
        assert _parse_pages("1-100", 5) == [0, 1, 2, 3, 4]

    def test_out_of_range_only(self) -> None:
        with pytest.raises(ValueError, match="no valid pages"):
            _parse_pages("50", 3)

    def test_invalid_string(self) -> None:
        with pytest.raises(ValueError, match="invalid"):
            _parse_pages("abc", 10)

    def test_reversed_range(self) -> None:
        with pytest.raises(ValueError, match="start > end"):
            _parse_pages("5-2", 10)

    def test_negative_page(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            _parse_pages("-1", 10)

    def test_negative_in_range(self) -> None:
        """A range like '-3-5' is malformed."""
        with pytest.raises(ValueError, match="invalid|>= 1"):
            _parse_pages("-3-5", 10)

    def test_zero_page(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            _parse_pages("0", 10)

    def test_deduplication(self) -> None:
        """Overlapping ranges should not produce duplicate indices."""
        assert _parse_pages("1-3,2-4", 10) == [0, 1, 2, 3]

    def test_too_many_pages(self) -> None:
        with pytest.raises(ValueError, match="max 20"):
            _parse_pages("1-25", 30)

    def test_whitespace_tolerance(self) -> None:
        assert _parse_pages(" 1 , 3 ", 5) == [0, 2]


# ---------------------------------------------------------------------------
# _format_page_list
# ---------------------------------------------------------------------------


class TestFormatPageList:
    def test_empty(self) -> None:
        assert _format_page_list([]) == ""

    def test_single(self) -> None:
        assert _format_page_list([0]) == "1"

    def test_consecutive_range(self) -> None:
        assert _format_page_list([0, 1, 2]) == "1-3"

    def test_mixed(self) -> None:
        assert _format_page_list([0, 1, 2, 5, 7, 8]) == "1-3, 6, 8-9"

    def test_non_consecutive(self) -> None:
        assert _format_page_list([0, 2, 4]) == "1, 3, 5"


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------


class TestPermission:
    tool = FileReadTool()

    def test_not_destructive(self) -> None:
        assert not self.tool.is_destructive({})

    def test_matcher_glob(self) -> None:
        matcher = self.tool.prepare_permission_matcher({"path": "src/main.py"})
        assert matcher("*.py")
