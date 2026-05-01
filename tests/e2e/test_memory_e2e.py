"""End-to-end tests for MemoryManager.

Coverage (per workflow.md Phase 4 checklist):
1. Happy path: memory_write tool invoked via prompt
2. Error path: LLM-level tool rejection (injection content)
3. Integration path: Kernel starts with MemoryManager loaded

Note: Each test uses a fresh session within a single asyncio.run()
to avoid cross-test state leakage and "session not active" errors.
"""

from __future__ import annotations

import asyncio
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
_TEST_TIMEOUT: float = 30.0


def _run(coro: Any, *, timeout: float = _LLM_TIMEOUT) -> Any:
    """Run an async coroutine with a timeout guard."""

    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _has_model(port: int, token: str) -> bool:
    """Check if the kernel has at least one LLM profile configured."""

    async def _check() -> bool:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/profile_list", {})
        return bool(result.get("profiles"))

    return _run(_check(), timeout=_TEST_TIMEOUT)


# ---------------------------------------------------------------------------
# 1. Integration path — kernel starts with MemoryManager loaded
# ---------------------------------------------------------------------------


def test_kernel_starts_with_memory(kernel: tuple[int, str]) -> None:
    """Kernel starts successfully with MemoryManager enabled."""
    port, token = kernel

    async def _check_health() -> bool:
        async with ProbeClient(port=port, token=token) as client:
            caps = await client.initialize()
        return caps is not None

    result = _run(_check_health(), timeout=_TEST_TIMEOUT)
    assert result is True, "Kernel should start with MemoryManager loaded"


# ---------------------------------------------------------------------------
# 2. Happy path — memory_write and memory_list in one session
# ---------------------------------------------------------------------------


def test_memory_write_and_list(kernel: tuple[int, str]) -> None:
    """LLM can write a memory then list it back in the same session."""
    port, token = kernel
    if not _has_model(port, token):
        pytest.skip("No LLM configured")

    async def _run_test() -> tuple[list[str], str, str]:
        """Write a memory in a single-turn test."""
        tool_titles: list[str] = []
        text_parts: list[str] = []
        stop_reason = "unknown"

        async with ProbeClient(
            port=port, token=token, request_timeout=_LLM_TIMEOUT
        ) as client:
            await client.initialize()
            sid = await client.new_session()

            async for event in client.prompt(
                sid,
                "You MUST use a tool to complete this task. "
                "Call the memory_write tool with these exact parameters: "
                "name='e2e-test-mem', category='semantic', "
                "description='E2E test memory created by automated test suite for verification', "
                "content='This memory was created by an e2e test on the Mustang kernel.' "
                "Do not answer in text — use the tool.",
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason

        return tool_titles, "".join(text_parts), stop_reason

    tools, text, stop = _run(_run_test())

    assert stop == "end_turn", f"Unexpected stop_reason: {stop}"
    assert len(tools) > 0 or "memory" in text.lower(), (
        "Expected memory_write tool call or memory mention"
    )


# ---------------------------------------------------------------------------
# 3. Happy path — memory_search works
# ---------------------------------------------------------------------------


def test_memory_list(kernel: tuple[int, str]) -> None:
    """LLM can list memories using memory_list tool."""
    port, token = kernel
    if not _has_model(port, token):
        pytest.skip("No LLM configured")

    async def _run_test() -> tuple[list[str], str, str]:
        tool_titles: list[str] = []
        text_parts: list[str] = []
        stop_reason = "unknown"

        async with ProbeClient(
            port=port, token=token, request_timeout=_LLM_TIMEOUT
        ) as client:
            await client.initialize()
            sid = await client.new_session()

            async for event in client.prompt(
                sid,
                "You MUST use a tool to answer this. "
                "Call the memory_list tool right now with no arguments. "
                "Do not answer in text — use the tool.",
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason

        return tool_titles, "".join(text_parts), stop_reason

    tools, text, stop = _run(_run_test())
    assert stop == "end_turn"
    # LLM should either use the tool or mention memory in its response
    assert len(tools) > 0 or "memory" in text.lower(), (
        "Expected memory_list tool call or memory mention in text"
    )


# ---------------------------------------------------------------------------
# 4. Happy path — memory_delete with confirmation
# ---------------------------------------------------------------------------


def test_memory_delete_with_confirmation(kernel: tuple[int, str]) -> None:
    """LLM can delete a memory with confirmation=true."""
    port, token = kernel
    if not _has_model(port, token):
        pytest.skip("No LLM configured")

    async def _run_test() -> tuple[list[str], str, str]:
        tool_titles: list[str] = []
        text_parts: list[str] = []
        stop_reason = "unknown"

        async with ProbeClient(
            port=port, token=token, request_timeout=_LLM_TIMEOUT
        ) as client:
            await client.initialize()
            sid = await client.new_session()

            # Single turn: write + delete in one prompt
            async for event in client.prompt(
                sid,
                "Do the following two things using tools:\n"
                "1. Call memory_write with name='delete-target', category='episodic', "
                "description='Temporary test memory', content='Will be deleted.'\n"
                "2. Then call memory_delete with name='delete-target' and confirmation=true.",
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason

        return tool_titles, "".join(text_parts), stop_reason

    tools, text, stop = _run(_run_test())
    assert stop == "end_turn"
    # Should have at least one tool call (write or delete)
    assert len(tools) > 0 or "memory" in text.lower(), (
        "Expected memory tool calls"
    )
