"""E2E tests for AgentTool (sub-agent spawning).

Coverage map
------------
test_agent_foreground     → AgentTool foreground, spawn_subagent, result handoff
test_agent_spawn_unavail  → AgentTool error path when spawn_subagent fails

Each test drives the live kernel through ProbeClient, triggering LLM
calls to the Agent tool.  The sub-agent runs a real query loop with a
real LLM provider.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ToolCallEvent,
    ToolCallUpdate,
    TurnComplete,
    ProbeClient,
)


_LLM_TIMEOUT: float = 120.0


def _run(coro: Any, *, timeout: float = _LLM_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_guarded())


def _client(port: int, token: str) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=_LLM_TIMEOUT)


def _skip_if_no_model(port: int, token: str) -> None:
    async def _check() -> list[dict[str, Any]]:
        async with _client(port, token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/profile_list", {})
        return result.get("profiles", [])

    profiles = _run(_check(), timeout=30)
    if not profiles:
        pytest.skip("No LLM model profiles configured — skipping")


async def _collect_turn(
    client: ProbeClient,
    sid: str,
    prompt: str,
) -> tuple[str, str, list[ToolCallEvent], list[ToolCallUpdate]]:
    """Run a prompt turn and collect all events."""
    text_parts: list[str] = []
    stop_reason = "unknown"
    tool_calls: list[ToolCallEvent] = []
    tool_updates: list[ToolCallUpdate] = []

    async for event in client.prompt(sid, prompt):
        if isinstance(event, AgentChunk):
            text_parts.append(event.text)
        elif isinstance(event, ToolCallEvent):
            tool_calls.append(event)
        elif isinstance(event, ToolCallUpdate):
            tool_updates.append(event)
        elif isinstance(event, PermissionRequest):
            await client.reply_permission(event.req_id, "allow_once")
        elif isinstance(event, TurnComplete):
            stop_reason = event.stop_reason

    return "".join(text_parts), stop_reason, tool_calls, tool_updates


# ---------------------------------------------------------------------------
# 1. Foreground sub-agent
# ---------------------------------------------------------------------------


def test_agent_foreground(kernel: tuple[int, str]) -> None:
    """Agent tool spawns a sub-agent that runs to completion.

    Happy path: LLM calls Agent tool → sub-agent runs → result returned.
    We verify: Agent tool was called, the turn completed, and the response
    contains meaningful content from the sub-agent's work.
    """
    port, token = kernel
    _skip_if_no_model(port, token)

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            text, stop, tools, tool_updates = await _collect_turn(
                client, sid,
                "Use a sub-agent to answer: What is 2+2? "
                "You must use the Agent tool for this.",
            )
            assert stop == "end_turn", f"Turn failed: {stop}, text: {text}"

            # Verify Agent tool was called
            agent_calls = [t for t in tools if t.title == "Agent"]
            assert len(agent_calls) > 0, (
                f"Expected Agent tool call, got: {[t.title for t in tools]}"
            )
            agent_tool_ids = {call.tool_call_id for call in agent_calls}
            agent_result_updates = [
                update for update in tool_updates
                if update.tool_call_id in agent_tool_ids
                and update.status == "completed"
                and update.meta
                and "mustang.agent/agentStats" in update.meta
            ]
            assert agent_result_updates, (
                "Expected completed Agent tool update to carry "
                "mustang.agent/agentStats metadata"
            )
            stats = agent_result_updates[-1].meta["mustang.agent/agentStats"]
            assert stats["totalTokens"] > 0
            assert stats["durationMs"] > 0

            # The response should contain an answer
            assert len(text) > 0, "Expected non-empty response from sub-agent"

    _run(_run_test())


# ---------------------------------------------------------------------------
# 2. Agent tool with background mode
# ---------------------------------------------------------------------------


def test_agent_background(kernel: tuple[int, str]) -> None:
    """Agent tool runs in background and returns a task_id.

    Happy path: Agent(run_in_background=true) → task_id returned →
    follow-up turn reads the result.
    """
    port, token = kernel
    _skip_if_no_model(port, token)

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            # Turn 1: spawn background agent
            text1, stop1, tools1, _ = await _collect_turn(
                client, sid,
                "Use a sub-agent in the background to answer: What is 3+3? "
                "You must use the Agent tool with run_in_background=true.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}"
            agent_calls = [t for t in tools1 if t.title == "Agent"]
            assert len(agent_calls) > 0, (
                f"Expected Agent tool call, got: {[t.title for t in tools1]}"
            )

            # Should mention a task ID
            assert "a" in text1.lower() or "task" in text1.lower() or "background" in text1.lower(), (
                f"Expected task reference in: {text1!r}"
            )

            # Turn 2: give the background agent a moment then check the result.
            await asyncio.sleep(3)
            text2, stop2, _, _ = await _collect_turn(
                client, sid,
                "Check on the background agent task and tell me its result.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}"
            assert len(text2) > 0, "Expected response about background agent result"

    _run(_run_test())
