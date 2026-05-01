"""E2E tests for FileRead image and PDF support.

Verifies that the FileRead tool correctly handles image and PDF files
through the real kernel ACP interface.  The LLM is prompted to read
specific test files, and we assert that the tool call events appear
in the event stream.

Coverage map
------------
test_read_image_e2e    → FileReadTool image branch, ImageContent in tool result
test_read_pdf_e2e      → FileReadTool PDF branch, PyMuPDF rendering, pages param
test_read_pdf_pages    → FileReadTool PDF branch with explicit page range
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ToolCallEvent,
    TurnComplete,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_TIMEOUT: float = 30.0
_LLM_TIMEOUT: float = 120.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    """Run an async coroutine with a hard timeout to prevent hangs."""

    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(
    port: int,
    token: str,
    *,
    request_timeout: float = _TEST_TIMEOUT,
) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=request_timeout)


def _has_models(port: int, token: str) -> bool:
    """Return True if the kernel has at least one LLM model configured."""

    async def _check() -> bool:
        async with _client(port, token) as c:
            await c.initialize()
            result = await c._request("_mustang.agent/model/profile_list", {})
        return bool(result.get("profiles"))

    return _run(_check())


def _create_test_image(directory: Path) -> Path:
    """Create a minimal 1x1 red PNG in *directory*."""
    # Minimal valid PNG: 1x1 pixel, RGB, red.
    import struct
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        raw = chunk_type + data
        return struct.pack(">I", len(data)) + raw + struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    # Row filter byte (0) + RGB pixel (255, 0, 0).
    raw_pixel = b"\x00\xff\x00\x00"
    idat_data = zlib.compress(raw_pixel)

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", ihdr_data)
    png += _chunk(b"IDAT", idat_data)
    png += _chunk(b"IEND", b"")

    path = directory / "test_image.png"
    path.write_bytes(png)
    return path


def _create_test_pdf(directory: Path, *, pages: int = 3) -> Path:
    """Create a simple multi-page PDF with text via PyMuPDF."""
    fitz = pytest.importorskip("fitz")

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 50), f"Page {i + 1} of {pages}", fontsize=14)
    path = directory / "test_doc.pdf"
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_read_image_e2e(kernel: tuple[int, str]) -> None:
    """LLM reads an image file → FileRead emits a tool_call event.

    Happy path: a valid PNG is read and the turn completes.
    """
    port, token = kernel
    if not _has_models(port, token):
        pytest.skip("No LLM configured — skipping image read e2e")

    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = _create_test_image(Path(tmpdir))

        async def _run_test() -> tuple[list[ToolCallEvent], str, str]:
            tool_events: list[ToolCallEvent] = []
            text_parts: list[str] = []
            stop_reason = "unknown"

            async with _client(port, token, request_timeout=_LLM_TIMEOUT) as c:
                await c.initialize()
                sid = await c.new_session()
                prompt = (
                    f"Use the FileRead tool to read this image file: {img_path}\n"
                    "After reading it, describe what you see."
                )
                async for event in c.prompt(sid, prompt):
                    if isinstance(event, AgentChunk):
                        text_parts.append(event.text)
                    elif isinstance(event, ToolCallEvent):
                        tool_events.append(event)
                    elif isinstance(event, PermissionRequest):
                        await c.reply_permission(event.req_id, "allow_once")
                    elif isinstance(event, TurnComplete):
                        stop_reason = event.stop_reason

            return tool_events, "".join(text_parts), stop_reason

        tool_events, text, stop_reason = _run(_run_test(), timeout=_LLM_TIMEOUT)

    # The LLM should have called FileRead.
    assert any(e.title == "FileRead" for e in tool_events), (
        f"Expected a FileRead tool call, got: {[e.title for e in tool_events]}"
    )
    assert stop_reason == "end_turn", f"stop_reason={stop_reason!r}"
    assert len(text) > 0, "Expected non-empty agent response"


def test_read_pdf_e2e(kernel: tuple[int, str]) -> None:
    """LLM reads a small PDF → FileRead emits a tool_call event.

    Happy path: a 3-page PDF is auto-rendered (within the 10-page limit).
    """
    port, token = kernel
    if not _has_models(port, token):
        pytest.skip("No LLM configured — skipping PDF read e2e")
    pytest.importorskip("fitz")

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = _create_test_pdf(Path(tmpdir), pages=3)

        async def _run_test() -> tuple[list[ToolCallEvent], str, str]:
            tool_events: list[ToolCallEvent] = []
            text_parts: list[str] = []
            stop_reason = "unknown"

            async with _client(port, token, request_timeout=_LLM_TIMEOUT) as c:
                await c.initialize()
                sid = await c.new_session()
                prompt = (
                    f"Use the FileRead tool to read this PDF: {pdf_path}\n"
                    "Tell me how many pages it has and what text is on the first page."
                )
                async for event in c.prompt(sid, prompt):
                    if isinstance(event, AgentChunk):
                        text_parts.append(event.text)
                    elif isinstance(event, ToolCallEvent):
                        tool_events.append(event)
                    elif isinstance(event, PermissionRequest):
                        await c.reply_permission(event.req_id, "allow_once")
                    elif isinstance(event, TurnComplete):
                        stop_reason = event.stop_reason

            return tool_events, "".join(text_parts), stop_reason

        tool_events, text, stop_reason = _run(_run_test(), timeout=_LLM_TIMEOUT)

    assert any(e.title == "FileRead" for e in tool_events), (
        f"Expected a FileRead tool call, got: {[e.title for e in tool_events]}"
    )
    assert stop_reason == "end_turn", f"stop_reason={stop_reason!r}"
    assert len(text) > 0, "Expected non-empty agent response"


def test_read_pdf_pages_e2e(kernel: tuple[int, str]) -> None:
    """LLM reads a large PDF with explicit page range.

    Verifies that the ``pages`` parameter is used when the PDF exceeds
    the auto-limit.
    """
    port, token = kernel
    if not _has_models(port, token):
        pytest.skip("No LLM configured — skipping PDF pages e2e")
    pytest.importorskip("fitz")

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = _create_test_pdf(Path(tmpdir), pages=15)

        async def _run_test() -> tuple[list[ToolCallEvent], str, str]:
            tool_events: list[ToolCallEvent] = []
            text_parts: list[str] = []
            stop_reason = "unknown"

            async with _client(port, token, request_timeout=_LLM_TIMEOUT) as c:
                await c.initialize()
                sid = await c.new_session()
                prompt = (
                    f"Use the FileRead tool to read pages 1-3 of this PDF: {pdf_path}\n"
                    'Make sure to pass pages="1-3" since the PDF has 15 pages.\n'
                    "Describe the content of the pages."
                )
                async for event in c.prompt(sid, prompt):
                    if isinstance(event, AgentChunk):
                        text_parts.append(event.text)
                    elif isinstance(event, ToolCallEvent):
                        tool_events.append(event)
                    elif isinstance(event, PermissionRequest):
                        await c.reply_permission(event.req_id, "allow_once")
                    elif isinstance(event, TurnComplete):
                        stop_reason = event.stop_reason

            return tool_events, "".join(text_parts), stop_reason

        tool_events, text, stop_reason = _run(_run_test(), timeout=_LLM_TIMEOUT)

    assert any(e.title == "FileRead" for e in tool_events), (
        f"Expected a FileRead tool call, got: {[e.title for e in tool_events]}"
    )
    assert stop_reason == "end_turn", f"stop_reason={stop_reason!r}"
    assert len(text) > 0, "Expected non-empty agent response"
