"""E2E tests for TodoWriteTool.

Coverage map
------------
test_todo_write_and_update → TodoWriteTool create + update, TaskRegistry._todos

Each test drives the live kernel through ProbeClient.
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
            result = await client._request("_mustang.agent/model/provider_list", {})
        return result.get("providers", [])

    providers = _run(_check(), timeout=30)
    if not providers:
        pytest.skip("No LLM providers configured — skipping")


async def _collect_turn(
    client: ProbeClient,
    sid: str,
    prompt: str,
) -> tuple[str, str, list[ToolCallEvent]]:
    """Run a prompt turn and collect events."""
    text_parts: list[str] = []
    stop_reason = "unknown"
    tool_calls: list[ToolCallEvent] = []

    async for event in client.prompt(sid, prompt):
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


def test_todo_write_and_update(kernel: tuple[int, str]) -> None:
    """TodoWriteTool creates and updates a todo list.

    Happy path: LLM calls TodoWrite to create items → turn completes →
    follow-up turn marks items completed.
    """
    port, token = kernel
    _skip_if_no_model(port, token)

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            # Turn 1: create a todo list.  Accept either direct TodoWrite
            # calls or REPL-wrapped ones (user config may enable REPL mode,
            # which hides TodoWrite from the LLM and routes it via REPL).
            text1, stop1, tools1 = await _collect_turn(
                client, sid,
                "Create a todo list with these two pending items using the "
                "TodoWrite tool: "
                "1) content='Write code' activeForm='Writing code', "
                "2) content='Run tests' activeForm='Running tests'. "
                "Each todo MUST include content, activeForm (present "
                "continuous), and status fields.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}, text: {text1}"

            todo_titles = {t.title for t in tools1}
            assert "TodoWrite" in todo_titles or "REPL" in todo_titles, (
                f"Expected TodoWrite (or REPL-wrapped) call, got: {todo_titles}"
            )

            # Turn 2: mark all completed.  Same loose check.
            text2, stop2, tools2 = await _collect_turn(
                client, sid,
                "Mark all todos as completed using TodoWrite.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}, text: {text2}"

            todo_titles2 = {t.title for t in tools2}
            assert "TodoWrite" in todo_titles2 or "REPL" in todo_titles2, (
                f"Expected TodoWrite (or REPL-wrapped) call in turn 2, got: {todo_titles2}"
            )

    _run(_run_test())
