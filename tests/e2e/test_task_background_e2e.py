"""E2E tests for background tasks (BashTool run_in_background + TaskOutputTool + TaskStopTool).

Coverage map
------------
test_bash_background_and_output → BashTool run_in_background, TaskOutputTool, TaskRegistry, notification drain
test_bash_background_stop       → BashTool run_in_background, TaskStopTool
test_task_output_invalid_id     → TaskOutputTool error path (nonexistent task)

Each test drives the live kernel through ProbeClient, triggering LLM
tool calls.  The LLM is expected to call BashTool with run_in_background
when instructed, and TaskOutputTool / TaskStopTool to read/stop tasks.
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
    """Run a prompt turn and collect all events.

    Returns (agent_text, stop_reason, tool_calls, tool_updates).
    Auto-allows all permission requests.
    """
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
# 1. Background bash + read output
# ---------------------------------------------------------------------------


def test_bash_background_and_output(kernel: tuple[int, str]) -> None:
    """Run a command in background, then read its output via TaskOutputTool.

    Happy path: BashTool(run_in_background=true) → task_id returned →
    wait → TaskOutputTool reads completed output.
    """
    port, token = kernel
    _skip_if_no_model(port, token)

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            # Turn 1: ask LLM to run a command in the background
            text1, stop1, tools1, _ = await _collect_turn(
                client, sid,
                "Run this exact command in the background: echo background_test_marker",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}, text: {text1}"

            # Verify Bash tool was called
            bash_calls = [t for t in tools1 if t.title == "Bash"]
            assert len(bash_calls) > 0, (
                f"Expected Bash tool call, got: {[t.title for t in tools1]}"
            )

            # The agent text should mention a task ID (starts with 'b')
            assert "b" in text1.lower() or "task" in text1.lower() or "background" in text1.lower(), (
                f"Expected mention of background task in: {text1!r}"
            )

            # Poll until 'echo' output appears in the session task files (usually <0.5s).
            from tests.e2e.conftest import poll_for_session_output
            await poll_for_session_output(sid, "background_test_marker", timeout=8.0, interval=0.2)
            text2, stop2, tools2, _ = await _collect_turn(
                client, sid,
                "Read the output of the background task you just started.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}, text: {text2}"

            # The output should contain our marker
            assert "background_test_marker" in text2, (
                f"Expected 'background_test_marker' in output, got: {text2!r}"
            )

    _run(_run_test())


# ---------------------------------------------------------------------------
# 2. Background bash + stop
# ---------------------------------------------------------------------------


def test_bash_background_stop(kernel: tuple[int, str]) -> None:
    """Start a long-running background command, then stop it.

    Verifies: BashTool background → TaskStopTool → task killed.
    """
    port, token = kernel
    _skip_if_no_model(port, token)

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            # Turn 1: start a long-running background command
            text1, stop1, tools1, _ = await _collect_turn(
                client, sid,
                "Run 'sleep 300' in the background.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}"
            bash_calls = [t for t in tools1 if t.title == "Bash"]
            assert len(bash_calls) > 0, "Expected Bash background call"

            # Turn 2: stop it
            text2, stop2, tools2, _ = await _collect_turn(
                client, sid,
                "Stop the background task you just started.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}"

            # Should have called TaskStop
            stop_calls = [t for t in tools2 if t.title in ("TaskStop", "KillShell")]
            assert len(stop_calls) > 0, (
                f"Expected TaskStop call, got: {[t.title for t in tools2]}"
            )
            assert "stop" in text2.lower() or "kill" in text2.lower() or "success" in text2.lower(), (
                f"Expected confirmation of stop in: {text2!r}"
            )

    _run(_run_test())


# ---------------------------------------------------------------------------
# 3. TaskOutputTool error path
# ---------------------------------------------------------------------------


def test_task_output_invalid_id(kernel: tuple[int, str]) -> None:
    """Asking for output of a nonexistent task returns an error, not a crash.

    Error path: TaskOutputTool with bogus task_id → error message in response.
    """
    port, token = kernel
    _skip_if_no_model(port, token)

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            text, stop, _, _ = await _collect_turn(
                client, sid,
                "Read the output of task 'b_nonexistent_99'.",
            )
            assert stop == "end_turn", f"Failed: {stop}"
            # The response should indicate the task wasn't found
            text_lower = text.lower()
            assert "not found" in text_lower or "no task" in text_lower or "error" in text_lower, (
                f"Expected error message about missing task, got: {text!r}"
            )

    _run(_run_test())
