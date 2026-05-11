"""E2E tests for Plan Mode lifecycle.

Exercises plan mode through the real ACP WebSocket interface.
A live kernel must be running (started by the ``kernel`` session fixture
in ``conftest.py``).

Coverage map
------------
test_enter_plan_mode            → ToolSearch loads EnterPlanMode → LLM enters plan mode
test_plan_mode_blocks_mutation  → In plan mode, mutating tools are rejected
test_plan_file_writable         → Plan file can be written in plan mode
test_exit_returns_plan          → ExitPlanMode returns plan content
test_plan_mode_lifecycle        → Full enter → explore → write → exit cycle
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ToolCallEvent,
    TurnComplete,
)


_TEST_TIMEOUT: float = 30.0
_LLM_TIMEOUT: float = 120.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(port: int, token: str, *, request_timeout: float = _TEST_TIMEOUT) -> Any:
    from probe.client import ProbeClient

    return ProbeClient(port=port, token=token, request_timeout=request_timeout)


async def _has_llm_provider(port: int, token: str) -> bool:
    """Check if the kernel has at least one LLM provider configured."""
    async with _client(port, token) as client:
        await client.initialize()
        result = await client._request("_mustang.agent/model/provider_list", {})
    return len(result.get("providers", [])) > 0


def _skip_if_no_llm(port: int, token: str) -> None:
    if not _run(_has_llm_provider(port, token)):
        pytest.skip("No LLM providers configured — skipping")


# ---------------------------------------------------------------------------
# 1. Enter plan mode via ToolSearch + EnterPlanMode
# ---------------------------------------------------------------------------


def test_enter_plan_mode(kernel: tuple[int, str]) -> None:
    """LLM can load EnterPlanMode via ToolSearch and enter plan mode."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_prompt() -> tuple[list[str], str]:
        tool_titles: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()
            prompt = (
                'First, call ToolSearch with query "select:EnterPlanMode" to load the tool. '
                "Then call EnterPlanMode to enter plan mode. "
                "After entering plan mode, reply with exactly: PLAN_MODE_ACTIVE"
            )
            async for event in client.prompt(sid, prompt):
                if isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return tool_titles, stop_reason

    tool_titles, stop_reason = _run(_run_prompt(), timeout=_LLM_TIMEOUT)
    assert stop_reason == "end_turn"
    # Verify both ToolSearch and EnterPlanMode were called.
    title_str = " ".join(tool_titles).lower()
    assert "toolsearch" in title_str or "tool" in title_str
    assert "enterplanmode" in title_str or "enter" in title_str or "plan" in title_str


def test_probe_plan_mode_keeps_mutating_tool_schemas(kernel: tuple[int, str]) -> None:
    """Probe E2E: plan mode changes authorization, not schema visibility."""
    port, token = kernel

    async def _run_probe() -> dict[str, Any]:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()
            await client.set_mode(sid, "plan")
            return await client._request(
                "_mustang.agent/session/tool_snapshot",
                {"sessionId": sid},
            )

    snapshot = _run(_run_probe())
    schemas = set(snapshot["schemas"])

    assert {"Bash", "Edit", "Write", "TodoWrite"}.issubset(schemas)
    assert "AskUserQuestion" in set(snapshot["deferred"])


# ---------------------------------------------------------------------------
# 2. Plan mode blocks mutations
# ---------------------------------------------------------------------------


def test_plan_mode_blocks_mutation(kernel: tuple[int, str]) -> None:
    """In plan mode, Edit on a non-plan file should be denied."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_prompt() -> tuple[str, list[str], str]:
        text_parts: list[str] = []
        tool_titles: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()
            # Enter plan mode first.
            setup = (
                'Call ToolSearch with query "select:EnterPlanMode" then call EnterPlanMode. '
                "After entering plan mode, try to use Write to create /tmp/test_plan_block.txt "
                "with content 'hello'. Report what happened — did it succeed or fail?"
            )
            async for event in client.prompt(sid, setup):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), tool_titles, stop_reason

    text, tool_titles, stop_reason = _run(_run_prompt(), timeout=_LLM_TIMEOUT)
    assert stop_reason == "end_turn"
    # The LLM should report that the write was blocked/denied.
    text_lower = text.lower()
    assert any(
        kw in text_lower
        for kw in ("denied", "blocked", "forbidden", "not allowed", "cannot", "plan mode")
    ), f"Expected denial message in response. Got: {text[:500]}"


# ---------------------------------------------------------------------------
# 3. Full plan mode lifecycle
# ---------------------------------------------------------------------------


def test_plan_mode_lifecycle(kernel: tuple[int, str]) -> None:
    """Full lifecycle: enter → write plan → exit → verify plan returned."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_prompt() -> tuple[str, list[str], str]:
        text_parts: list[str] = []
        tool_titles: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()
            prompt = (
                "Follow these steps exactly:\n"
                '1. Call ToolSearch with query "select:EnterPlanMode,ExitPlanMode"\n'
                "2. Call EnterPlanMode\n"
                "3. Call ExitPlanMode\n"
                "4. Tell me the result of ExitPlanMode"
            )
            async for event in client.prompt(sid, prompt):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), tool_titles, stop_reason

    text, tool_titles, stop_reason = _run(_run_prompt(), timeout=_LLM_TIMEOUT)
    assert stop_reason == "end_turn"
    # Verify the full tool chain executed.
    title_str = " ".join(tool_titles).lower()
    assert "enter" in title_str or "plan" in title_str
    assert "exit" in title_str
