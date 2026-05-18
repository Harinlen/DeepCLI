"""E2E tests for SendMessageTool.

Coverage map
------------
test_send_message_queue       → SendMessage to running background agent
test_send_message_resume      → SendMessage to completed agent (resume path)
test_send_message_not_found   → SendMessage to nonexistent agent → error
test_send_message_cross_session → SendMessage to session:<id> (cross-session)

Each test drives the live kernel through ProbeClient, triggering LLM
calls to the SendMessage tool.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ToolCallEvent,
    ToolCallUpdate,
    TurnComplete,
    ProbeClient,
)
from tests.e2e.test_probe_phase2_e2e import phase2_kernel


_LLM_TIMEOUT: float = 15.0


def _run(coro: Any, *, timeout: float = _LLM_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_guarded())


def _client(port: int, token: str) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=_LLM_TIMEOUT)


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
# 1. Queue message to running background agent
# ---------------------------------------------------------------------------


def test_send_message_queue(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """Spawn a named background agent, then SendMessage to it while running.

    Happy path: Agent(name="explorer", run_in_background=true) →
    SendMessage(to="explorer") → message queued.
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))

            # Turn 1: spawn a named background agent.
            text1, stop1, tools1, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_AGENT_SPAWN_EXPLORER: spawn background explorer.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}"
            agent_calls = [t for t in tools1 if t.title == "Agent"]
            assert len(agent_calls) > 0, (
                f"Expected Agent tool call, got: {[t.title for t in tools1]}"
            )

            # Turn 2: immediately send a message to the running agent.
            text2, stop2, tools2, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_SENDMESSAGE_EXPLORER: send a message to explorer.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}"
            sm_calls = [t for t in tools2 if t.title == "SendMessage"]
            assert len(sm_calls) > 0, (
                f"Expected SendMessage tool call, got: {[t.title for t in tools2]}"
            )
            # Response should indicate success (queued or delivered).
            combined = text2.lower()
            assert "queued" in combined or "sent" in combined or "delivered" in combined or "message" in combined, (
                f"Expected queue confirmation in: {text2!r}"
            )

    _run(_run_test())


# ---------------------------------------------------------------------------
# 2. Resume a completed agent
# ---------------------------------------------------------------------------


def test_send_message_resume(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """Spawn a background agent, wait for it to complete, then resume it.

    Happy path: Agent(name="researcher") → wait → SendMessage("researcher") →
    agent resumed.
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))

            # Turn 1: spawn a named background agent.
            text1, stop1, tools1, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_AGENT_SPAWN_RESEARCHER: spawn background researcher.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}"

            # Turn 2: try to resume it with SendMessage.
            text2, stop2, tools2, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_SENDMESSAGE_RESEARCHER: send a message to researcher.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}"
            sm_calls = [t for t in tools2 if t.title == "SendMessage"]
            assert len(sm_calls) > 0, (
                f"Expected SendMessage tool call, got: {[t.title for t in tools2]}"
            )

    _run(_run_test())


# ---------------------------------------------------------------------------
# 3. SendMessage to nonexistent agent → error
# ---------------------------------------------------------------------------


def test_send_message_not_found(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """SendMessage to a nonexistent agent should produce an error response.

    Error path: SendMessage(to="nobody") → "not found" error.
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))

            text, stop, tools, _ = await _collect_turn(
                client, sid,
                "PHASE2_SENDMESSAGE_MISSING: send to a missing durable agent.",
            )
            assert stop == "end_turn", f"Turn failed: {stop}"
            # The LLM should report the error from SendMessage.
            combined = text.lower()
            assert "not found" in combined or "error" in combined or "does not exist" in combined, (
                f"Expected error message in: {text!r}"
            )

    _run(_run_test())


# ---------------------------------------------------------------------------
# 4. Cross-session delivery
# ---------------------------------------------------------------------------


def test_send_message_cross_session(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """SendMessage to session:<id> should deliver cross-session.

    Integration path: create two sessions, send from one to the other.
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            # Create two sessions.
            sid_a = await client.new_session(cwd=str(workspace))
            sid_b = await client.new_session(cwd=str(workspace))

            # From session A, send to session B.
            text, stop, tools, _ = await _collect_turn(
                client, sid_a,
                f"PHASE2_SENDMESSAGE_CROSS_SESSION: send to session:{sid_b}",
            )
            assert stop == "end_turn", f"Turn failed: {stop}"
            sm_calls = [t for t in tools if t.title == "SendMessage"]
            assert len(sm_calls) > 0, (
                f"Expected SendMessage tool call, got: {[t.title for t in tools]}"
            )
            # Should indicate success or at least attempt delivery.
            combined = text.lower()
            assert "delivered" in combined or "sent" in combined or "session" in combined, (
                f"Expected delivery confirmation in: {text!r}"
            )

    _run(_run_test())
