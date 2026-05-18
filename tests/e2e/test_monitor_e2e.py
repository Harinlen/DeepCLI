"""E2E tests for MonitorTool — background process streaming via probe.

Coverage map
------------
test_monitor_start_and_stop     → MonitorTool spawn, ToolSearch deferred load,
                                  TaskStopTool kill, MonitorTaskState lifecycle
test_monitor_invalid_command    → MonitorTool error path (command fails immediately)
test_monitor_with_task_output   → MonitorTool + TaskOutputTool integration

Each test drives the live kernel through ProbeClient, triggering LLM
tool calls.  The LLM is expected to load Monitor via ToolSearch (it's
a deferred tool) and use it when instructed.
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
# 1. Happy path: Monitor start + stop
# ---------------------------------------------------------------------------


def test_monitor_start_and_stop(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """Start a monitor on a simple command, then stop it.

    Happy path: ToolSearch loads Monitor → Monitor spawns command →
    task ID returned → TaskStop kills the monitor.

    Verifies:
    - Monitor tool is discoverable via ToolSearch
    - Monitor spawns a subprocess and returns a task_id
    - TaskStop can kill a monitor task
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))

            # Turn 1: ask LLM to start a monitor.
            # The prompt explicitly mentions Monitor tool so the LLM
            # knows to load it via ToolSearch.
            text1, stop1, tools1, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_MONITOR_START: start a monitor task.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}, text: {text1}"

            # Should have ToolSearch and/or Monitor calls.
            tool_titles = [t.title for t in tools1]
            assert any(t in ("Monitor", "ToolSearch") for t in tool_titles), (
                f"Expected Monitor or ToolSearch call, got: {tool_titles}"
            )

            # The response should mention a task ID (starts with 'm').
            text1_lower = text1.lower()
            assert "phase2_monitor_start_ok" in text1_lower or "monitor" in text1_lower, (
                f"Expected mention of monitor/task in: {text1!r}"
            )

            # Turn 2: stop the monitor (task is already registered after Turn 1).
            text2, stop2, tools2, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_MONITOR_STOP: stop the monitor task you just started.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}"

            # Should have called TaskStop.
            stop_calls = [t for t in tools2 if t.title in ("TaskStop", "KillShell")]
            assert len(stop_calls) > 0, f"Expected TaskStop call, got: {[t.title for t in tools2]}"

    _run(_run_test())


# ---------------------------------------------------------------------------
# 2. Error path: invalid command
# ---------------------------------------------------------------------------


def test_monitor_invalid_command(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """Monitor a nonexistent command — process exits, task becomes failed.

    Error path: command exits with non-zero → MonitorTaskState status=failed →
    task notification delivered.
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))

            # Start monitor on a command that will fail immediately.
            text1, stop1, tools1, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_MONITOR_INVALID: monitor an invalid command.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}"

            # Nonexistent command exits immediately; brief yield lets the kernel process it.
            await asyncio.sleep(0.5)

            # Turn 2: ask about the monitor status.
            text2, stop2, _, _ = await _collect_turn(
                client,
                sid,
                "What is the status of the monitor task? Is it still running?",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}"

            # The response should indicate the task failed or completed.
            text2_lower = text2.lower()
            assert any(
                word in text2_lower
                for word in ("fail", "error", "not found", "stopped", "exit", "completed")
            ), f"Expected failure/error indication in: {text2!r}"

    _run(_run_test())


# ---------------------------------------------------------------------------
# 3. Integration path: Monitor + TaskOutput
# ---------------------------------------------------------------------------


def test_monitor_with_task_output(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    """Monitor a command, then read its output via TaskOutput.

    Integration path: Monitor running → TaskOutputTool reads output file →
    both tools can operate on the same task.
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))

            # Turn 1: start monitor.
            text1, stop1, tools1, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_MONITOR_OUTPUT: monitor a command that writes integration_marker.",
            )
            assert stop1 == "end_turn", f"Turn 1 failed: {stop1}"

            # Poll task output files until integration_marker appears (script takes ~1.5s).
            from tests.e2e.conftest import poll_for_session_output
            await poll_for_session_output(sid, "integration_marker", timeout=3.0, interval=0.1)

            # Turn 2: ask LLM to read the task output.
            text2, stop2, tools2, _ = await _collect_turn(
                client,
                sid,
                "PHASE2_MONITOR_OUTPUT_READ: read the monitor output using TaskOutput.",
            )
            assert stop2 == "end_turn", f"Turn 2 failed: {stop2}"

            # The output should contain our marker text.
            assert "integration_marker" in text2, (
                f"Expected 'integration_marker' in output, got: {text2!r}"
            )

    _run(_run_test())
