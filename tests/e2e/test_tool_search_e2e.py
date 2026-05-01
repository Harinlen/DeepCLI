"""E2E tests for ToolSearch + deferred tool loading.

Exercises the ToolSearch tool through the real ACP WebSocket interface.
A live kernel must be running (started by the ``kernel`` session fixture
in ``conftest.py``).

Coverage map
------------
test_tool_search_registered         → ToolManager startup, ToolSearchTool registration
test_tool_search_no_match_graceful  → ToolSearch called with no deferred match
test_tool_search_select_deferred    → ToolSearch loads a deferred tool (requires deferred tools)
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


# Timeout for non-LLM operations.
_TEST_TIMEOUT: float = 30.0
# Timeout for tests that include LLM round-trips.
_LLM_TIMEOUT: float = 90.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    """Run an async coroutine with a hard timeout to prevent hangs."""
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_guarded())


def _client(
    port: int, token: str, *, request_timeout: float = _TEST_TIMEOUT,
) -> Any:
    """Create a ProbeClient with the e2e request timeout."""
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
# 1. ToolSearch is registered and visible to the LLM
# ---------------------------------------------------------------------------


def test_tool_search_registered(kernel: tuple[int, str]) -> None:
    """ToolSearch should be registered at startup and appear in the
    session's available tool pool.

    We verify indirectly: a kernel with ToolSearch registered starts
    without error, and a trivial prompt completes.  The schema list
    sent to the LLM includes ToolSearch (verified by unit tests; here
    we just confirm no startup crash from the registration path).
    """
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_prompt() -> str:
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()
            stop_reason = "unknown"
            async for event in client.prompt(sid, "Reply with exactly: ok"):
                if isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return stop_reason

    stop_reason = _run(_run_prompt(), timeout=_LLM_TIMEOUT)
    assert stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# 2. ToolSearch handles no-match gracefully
# ---------------------------------------------------------------------------


def test_tool_search_no_match_graceful(kernel: tuple[int, str]) -> None:
    """When the LLM calls ToolSearch with a non-existent tool name,
    the tool should complete without error and the conversation should
    continue normally.
    """
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
                'Call the ToolSearch tool with query "select:ZzzNonexistentTool999" '
                "and then tell me what the result was. "
                "Reply with the exact result text you received."
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
    # The LLM should have called ToolSearch (visible in tool_titles).
    assert any("ToolSearch" in t or "tool" in t.lower() for t in tool_titles), (
        f"Expected ToolSearch to be called. Tool titles seen: {tool_titles}"
    )


# ---------------------------------------------------------------------------
# 3. ToolSearch loads a deferred tool (requires deferred tools registered)
# ---------------------------------------------------------------------------


def test_tool_search_select_deferred(kernel: tuple[int, str]) -> None:
    """ToolSearch with a valid deferred tool name should return its
    schema and promote it to core for the next turn.
    """
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
                'Call the ToolSearch tool with query "select:EnterPlanMode" '
                "and tell me if the schema was loaded successfully."
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
    assert any("ToolSearch" in t for t in tool_titles)
    # The response should mention the loaded schema.
    assert "EnterPlanMode" in text


# ---------------------------------------------------------------------------
# 4. Deferred listing actually triggers ToolSearch unlock for web tools
# ---------------------------------------------------------------------------


def test_deferred_listing_unlocks_web_tools(kernel: tuple[int, str]) -> None:
    """When the user asks a question that requires a deferred web tool,
    the LLM should:

      1. Recognise from the deferred listing that WebSearch/WebFetch exist.
      2. Call ToolSearch to load their schemas.
      3. Then call WebSearch (or WebFetch) to answer.

    This is the closure-seam probe for the whole deferred-tool flow:
    registry → deferred_listing system-reminder → LLM → ToolSearch →
    promote → WebSearch.  Regression here means the LLM hits the
    "我没有 web search 工具" failure mode again.
    """
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_prompt() -> tuple[list[str], str]:
        tool_titles: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()
            prompt = (
                "Search the web for the latest news about the SpaceX Starship program. "
                "Use whatever web tools are available."
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

    assert stop_reason == "end_turn", f"unexpected stop_reason={stop_reason}"
    # The LLM must have called ToolSearch first to unlock WebSearch/WebFetch.
    assert any("ToolSearch" in t for t in tool_titles), (
        f"Expected ToolSearch to be called for web query. Tools seen: {tool_titles}"
    )
    # And then actually used a web tool.
    assert any(("WebSearch" in t) or ("WebFetch" in t) for t in tool_titles), (
        f"Expected WebSearch or WebFetch after unlock. Tools seen: {tool_titles}"
    )
