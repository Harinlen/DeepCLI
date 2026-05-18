"""E2E tests for Read using real sample assets.

Uses ``tests/assert/sample.png`` and ``tests/assert/sample.pdf`` to
verify the full kernel pipeline with real image and PDF files.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ToolCallEvent,
    TurnComplete,
)
from tests.e2e.test_probe_phase2_e2e import phase2_kernel

_TEST_TIMEOUT: float = 15.0
_ASSETS = Path(__file__).parents[1] / "assert"


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(port: int, token: str) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT)


def test_read_real_png(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """Read tests/assert/sample.png through live kernel."""
    port, token, workspace, _home = phase2_kernel

    img_path = _ASSETS / "sample.png"
    assert img_path.exists(), f"Missing test asset: {img_path}"

    async def _run_test() -> tuple[list[ToolCallEvent], str, str]:
        tool_events: list[ToolCallEvent] = []
        text_parts: list[str] = []
        stop_reason = "unknown"

        async with _client(port, token) as c:
            await c.initialize()
            sid = await c.new_session(cwd=str(workspace))
            async for event in c.prompt(
                sid,
                f"PHASE2_REAL_PNG_READ {img_path}",
                timeout=_TEST_TIMEOUT,
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_events.append(event)
                elif isinstance(event, PermissionRequest):
                    await c.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason

        return tool_events, "".join(text_parts), stop_reason

    tool_events, text, stop_reason = _run(_run_test())

    assert any(e.title == "Read" for e in tool_events), (
        f"Expected Read call, got: {[e.title for e in tool_events]}"
    )
    assert stop_reason == "end_turn"
    assert "PHASE2_REAL_PNG_READ_OK" in text


def test_read_real_pdf(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """Read tests/assert/sample.pdf through live kernel."""
    port, token, workspace, _home = phase2_kernel

    pdf_path = _ASSETS / "sample.pdf"
    assert pdf_path.exists(), f"Missing test asset: {pdf_path}"

    async def _run_test() -> tuple[list[ToolCallEvent], str, str]:
        tool_events: list[ToolCallEvent] = []
        text_parts: list[str] = []
        stop_reason = "unknown"

        async with _client(port, token) as c:
            await c.initialize()
            sid = await c.new_session(cwd=str(workspace))
            async for event in c.prompt(
                sid,
                f"PHASE2_REAL_PDF_READ {pdf_path}",
                timeout=_TEST_TIMEOUT,
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_events.append(event)
                elif isinstance(event, PermissionRequest):
                    await c.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason

        return tool_events, "".join(text_parts), stop_reason

    tool_events, text, stop_reason = _run(_run_test())

    assert any(e.title == "Read" for e in tool_events), (
        f"Expected Read call, got: {[e.title for e in tool_events]}"
    )
    assert stop_reason == "end_turn"
    assert "PHASE2_REAL_PDF_READ_OK" in text
    print(f"\n  LLM response: {text[:200]}")
