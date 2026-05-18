"""E2E tests for ListMcpResources + ReadMcpResource tools via run-kernel probe."""

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


async def _run_prompt(port: int, token: str, workspace: Path, prompt: str) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    tool_titles: list[str] = []
    async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
        await client.initialize()
        sid = await client.new_session(cwd=str(workspace))
        async for event in client.prompt(sid, prompt, timeout=_TEST_TIMEOUT):
            if isinstance(event, AgentChunk):
                text_parts.append(event.text)
            elif isinstance(event, ToolCallEvent):
                tool_titles.append(event.title)
            elif isinstance(event, PermissionRequest):
                await client.reply_permission(event.req_id, "allow_once")
            elif isinstance(event, TurnComplete):
                assert event.stop_reason == "end_turn"
    return "".join(text_parts), tool_titles


def test_list_mcp_resources(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    port, token, workspace, _home = phase2_kernel
    text, tool_titles = _run(_run_prompt(port, token, workspace, "PHASE2_MCP_LIST"))
    assert "ListMcpResources" in tool_titles
    assert "PHASE2_MCP_LIST_OK" in text


def test_read_mcp_resource_text(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    port, token, workspace, _home = phase2_kernel
    text, tool_titles = _run(_run_prompt(port, token, workspace, "PHASE2_MCP_READ"))
    assert "ReadMcpResource" in tool_titles
    assert "PHASE2_MCP_READ_OK" in text


def test_read_mcp_resource_blob(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    port, token, workspace, _home = phase2_kernel
    text, tool_titles = _run(_run_prompt(port, token, workspace, "PHASE2_MCP_BLOB"))
    assert "ReadMcpResource" in tool_titles
    assert "PHASE2_MCP_BLOB_OK" in text
    assert ".png" in text
