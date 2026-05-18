"""E2E tests for MemoryManager through run-kernel probe."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from probe.client import AgentChunk, PermissionRequest, ProbeClient, ToolCallEvent, TurnComplete
from tests.e2e.test_probe_phase2_e2e import phase2_kernel

_TEST_TIMEOUT = 15.0


def _run(coro: Any) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=_TEST_TIMEOUT)
    return asyncio.run(_guarded())


async def _prompt(port: int, token: str, workspace: Path, text: str) -> tuple[str, list[str], str]:
    text_parts: list[str] = []
    tools: list[str] = []
    stop_reason = "unknown"
    async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
        await client.initialize()
        sid = await client.new_session(cwd=str(workspace))
        async for event in client.prompt(sid, text, timeout=_TEST_TIMEOUT):
            if isinstance(event, AgentChunk):
                text_parts.append(event.text)
            elif isinstance(event, ToolCallEvent):
                tools.append(event.title)
            elif isinstance(event, PermissionRequest):
                await client.reply_permission(event.req_id, "allow_once")
            elif isinstance(event, TurnComplete):
                stop_reason = event.stop_reason
    return "".join(text_parts), tools, stop_reason


def test_kernel_starts_with_memory(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    port, token, _workspace, _home = phase2_kernel
    async def _check() -> bool:
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            return bool(await client.initialize())
    assert _run(_check()) is True


def test_memory_write_and_list(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    port, token, workspace, _home = phase2_kernel
    text, tools, stop = _run(_prompt(port, token, workspace, "PHASE2_MEMORY_WRITE"))
    assert stop == "end_turn"
    assert "memory_write" in tools
    assert "PHASE2_MEMORY_WRITE_OK" in text

    text, tools, stop = _run(_prompt(port, token, workspace, "PHASE2_MEMORY_LIST"))
    assert stop == "end_turn"
    assert "memory_list" in tools
    assert "PHASE2_MEMORY_LIST_OK" in text


def test_memory_list(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    port, token, workspace, _home = phase2_kernel
    text, tools, stop = _run(_prompt(port, token, workspace, "PHASE2_MEMORY_LIST"))
    assert stop == "end_turn"
    assert "memory_list" in tools
    assert "PHASE2_MEMORY_LIST_OK" in text


def test_memory_delete_with_confirmation(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    port, token, workspace, _home = phase2_kernel
    _run(_prompt(port, token, workspace, "PHASE2_MEMORY_WRITE"))
    text, tools, stop = _run(_prompt(port, token, workspace, "PHASE2_MEMORY_DELETE"))
    assert stop == "end_turn"
    assert "memory_delete" in tools
    assert "PHASE2_MEMORY_DELETE_OK" in text
