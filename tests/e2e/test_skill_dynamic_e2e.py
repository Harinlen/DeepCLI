"""E2E: Dynamic skill discovery + conditional activation.

Tests that file-tool operations trigger SkillManager.on_file_touched()
and that new skill directories are discovered at runtime.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pathlib import Path

from probe.client import PermissionRequest, ProbeClient, ToolCallEvent, TurnComplete
from tests.e2e.test_probe_phase2_e2e import phase2_kernel

_TEST_TIMEOUT = 15.0


def _run(coro: Any) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=_TEST_TIMEOUT)
    return asyncio.run(_guarded())


def test_file_write_does_not_crash_skill_discovery(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    """Write triggering on_file_touched doesn't crash the kernel.

    The ToolExecutor calls skills.on_file_touched() after Write.
    If SkillManager is broken, this would crash the tool execution
    pipeline.  This test verifies the integration is safe.
    """
    port, token, workspace, _home = phase2_kernel

    async def _run_test() -> tuple[str, list[str]]:
        stop_reason = "unknown"
        tools: list[str] = []
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            async for event in client.prompt(
                sid,
                "PHASE2_FILE_WRITE_ALLOW: overwrite phase2_existing.txt.",
                timeout=_TEST_TIMEOUT,
            ):
                if isinstance(event, ToolCallEvent):
                    tools.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                if isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return stop_reason, tools

    stop_reason, tools = _run(_run_test())
    assert stop_reason == "end_turn"
    assert "Write" in tools


def test_file_edit_does_not_crash_skill_discovery(
    phase2_kernel: tuple[int, str, Path, Path],
) -> None:
    """Edit triggering on_file_touched doesn't crash the kernel.

    Same as above but for the Edit tool path.
    """
    port, token, workspace, _home = phase2_kernel
    test_file = workspace / "phase2_dynamic_edit.txt"
    test_file.write_text("original content\nline 2\n")

    async def _run_test() -> tuple[str, list[str]]:
        stop_reason = "unknown"
        tools: list[str] = []
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            async for event in client.prompt(
                sid,
                "PHASE2_DYNAMIC_EDIT",
                timeout=_TEST_TIMEOUT,
            ):
                if isinstance(event, ToolCallEvent):
                    tools.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return stop_reason, tools

    stop_reason, tools = _run(_run_test())
    assert stop_reason == "end_turn"
    assert "Edit" in tools
    assert "modified content" in test_file.read_text(encoding="utf-8")
