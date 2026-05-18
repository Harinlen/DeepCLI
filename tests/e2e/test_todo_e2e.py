"""E2E tests for TodoWriteTool.

Coverage map
------------
test_todo_write_and_update → TodoWriteTool create + update, TaskRegistry._todos

Each test drives the live kernel through ProbeClient.
"""

from __future__ import annotations

import asyncio
from typing import Any

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ToolCallEvent,
    TurnComplete,
    ProbeClient,
)
from tests.e2e.test_probe_phase2_e2e import phase2_kernel


_TEST_TIMEOUT: float = 15.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_guarded())


def _client(port: int, token: str) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT)


async def _collect_turn(
    client: ProbeClient,
    sid: str,
    prompt: str,
) -> tuple[str, str, list[ToolCallEvent]]:
    """Run a prompt turn and collect events."""
    text_parts: list[str] = []
    stop_reason = "unknown"
    tool_calls: list[ToolCallEvent] = []

    async for event in client.prompt(sid, prompt, timeout=_TEST_TIMEOUT):
        if isinstance(event, AgentChunk):
            text_parts.append(event.text)
        elif isinstance(event, ToolCallEvent):
            tool_calls.append(event)
        elif isinstance(event, PermissionRequest):
            await client.reply_permission(event.req_id, "allow_once")
        elif isinstance(event, TurnComplete):
            stop_reason = event.stop_reason

    return "".join(text_parts), stop_reason, tool_calls


# ---------------------------------------------------------------------------
# 1. TodoWrite create + update
# ---------------------------------------------------------------------------


def test_todo_write_and_update(phase2_kernel: tuple[int, str, Any, Any]) -> None:
    """TodoWriteTool creates and updates a todo list.

    Happy path: LLM calls TodoWrite to create items → turn completes →
    follow-up turn marks items completed.
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))

            # Turn 1: create a todo list.  Accept either direct TodoWrite
            # calls or REPL-wrapped ones (user config may enable REPL mode,
            # which hides TodoWrite from the LLM and routes it via REPL).
            text1, stop1, tools1 = await _collect_turn(
                client, sid,
                "PHASE2_TODO: create a todo list.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}, text: {text1}"

            todo_titles = {t.title for t in tools1}
            assert "TodoWrite" in todo_titles, (
                f"Expected TodoWrite call, got: {todo_titles}"
            )
            assert "PHASE2_TODO_OK" in text1

            # Turn 2: mark all completed.  Same loose check.
            text2, stop2, tools2 = await _collect_turn(
                client, sid,
                "PHASE2_TODO_UPDATE: mark all todos completed.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}, text: {text2}"

            todo_titles2 = {t.title for t in tools2}
            assert "TodoWrite" in todo_titles2, (
                f"Expected TodoWrite call in turn 2, got: {todo_titles2}"
            )
            assert "PHASE2_TODO_UPDATE_OK" in text2

    _run(_run_test())
