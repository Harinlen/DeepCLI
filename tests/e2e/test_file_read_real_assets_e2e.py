"""E2E tests for Read using real sample assets.

Uses ``tests/assert/sample.png`` and ``tests/assert/sample.pdf`` to
verify the full kernel pipeline with real image and PDF files.
"""

from __future__ import annotations

import asyncio
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

_LLM_TIMEOUT: float = 120.0
_ASSETS = Path(__file__).parents[1] / "assert"


def _run(coro: Any, *, timeout: float = _LLM_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(port: int, token: str) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=_LLM_TIMEOUT)


def _has_models(port: int, token: str) -> bool:
    async def _check() -> bool:
        async with _client(port, token) as c:
            await c.initialize()
            result = await c._request("_mustang.agent/model/profile_list", {})
        return bool(result.get("profiles"))

    return _run(_check(), timeout=30)


def test_read_real_png(kernel: tuple[int, str]) -> None:
    """Read tests/assert/sample.png through live kernel."""
    port, token = kernel
    if not _has_models(port, token):
        pytest.skip("No LLM configured")

    img_path = _ASSETS / "sample.png"
    assert img_path.exists(), f"Missing test asset: {img_path}"

    async def _run_test() -> tuple[list[ToolCallEvent], str, str]:
        tool_events: list[ToolCallEvent] = []
        text_parts: list[str] = []
        stop_reason = "unknown"

        async with _client(port, token) as c:
            await c.initialize()
            sid = await c.new_session()
            async for event in c.prompt(
                sid,
                f"Use the Read tool to read this image: {img_path}\n"
                "Describe what you see. Be concise (1-2 sentences).",
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
    assert len(text) > 0
    print(f"\n  LLM response: {text[:200]}")


def test_read_real_pdf(kernel: tuple[int, str]) -> None:
    """Read tests/assert/sample.pdf through live kernel."""
    port, token = kernel
    if not _has_models(port, token):
        pytest.skip("No LLM configured")

    pdf_path = _ASSETS / "sample.pdf"
    assert pdf_path.exists(), f"Missing test asset: {pdf_path}"

    async def _run_test() -> tuple[list[ToolCallEvent], str, str]:
        tool_events: list[ToolCallEvent] = []
        text_parts: list[str] = []
        stop_reason = "unknown"

        async with _client(port, token) as c:
            await c.initialize()
            sid = await c.new_session()
            async for event in c.prompt(
                sid,
                f"Use the Read tool to read this PDF: {pdf_path}\n"
                "Summarize the content. Be concise (2-3 sentences).",
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
    assert len(text) > 0
    print(f"\n  LLM response: {text[:200]}")
