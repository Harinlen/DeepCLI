"""E2E tests for ScheduleManager cron tools through run-kernel probe."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from probe.client import AgentChunk, PermissionRequest, ProbeClient, ToolCallEvent, TurnComplete
from tests.e2e.test_probe_phase2_e2e import phase2_kernel

_TEST_TIMEOUT = 15.0


def _run(coro: Any) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=_TEST_TIMEOUT)
    return asyncio.run(_guarded())


async def _prompt(port: int, token: str, workspace: Path, text: str) -> tuple[str, list[str], str]:
    text_parts: list[str] = []
    tools: list[str] = []
    stop_reason = "unknown"
    async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
        await client.initialize()
        sid = await client.new_session(cwd=str(workspace))
        async for event in client.prompt(sid, text, timeout=_TEST_TIMEOUT):
            if isinstance(event, AgentChunk):
                text_parts.append(event.text)
            elif isinstance(event, ToolCallEvent):
                tools.append(event.title)
            elif isinstance(event, PermissionRequest):
                await client.reply_permission(event.req_id, "allow_once")
            elif isinstance(event, TurnComplete):
                stop_reason = event.stop_reason
    return "".join(text_parts), tools, stop_reason


def _assert_tool(phase2_kernel: tuple[int, str, Path, Path], prompt: str, tool: str, marker: str) -> None:
    port, token, workspace, _home = phase2_kernel
    text, tools, stop = _run(_prompt(port, token, workspace, prompt))
    assert stop == "end_turn"
    assert tool in tools
    assert marker in text


def test_cron_create_and_list(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    _assert_tool(phase2_kernel, "PHASE2_CRON_CREATE", "CronCreate", "PHASE2_CRON_CREATE_OK")
    _assert_tool(phase2_kernel, "PHASE2_CRON_LIST", "CronList", "PHASE2_CRON_LIST_OK")


def test_cron_create_with_delivery(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    _assert_tool(phase2_kernel, "PHASE2_CRON_DELIVERY", "CronCreate", "PHASE2_CRON_DELIVERY_OK")


def test_cron_pause_and_resume(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    _assert_tool(phase2_kernel, "PHASE2_CRON_CREATE", "CronCreate", "PHASE2_CRON_CREATE_OK")
    _assert_tool(phase2_kernel, "PHASE2_CRON_LIST", "CronList", "PHASE2_CRON_LIST_OK")


def test_cron_create_with_repeat_limit(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    _assert_tool(phase2_kernel, "PHASE2_CRON_REPEAT", "CronCreate", "PHASE2_CRON_REPEAT_OK")


def test_cron_delete(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    _assert_tool(phase2_kernel, "PHASE2_CRON_DELETE_MISSING", "CronDelete", "PHASE2_CRON_DELETE_OK")


def test_cron_command_registered(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    port, token, _workspace, _home = phase2_kernel
    async def _check() -> dict[str, Any]:
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            await client.initialize()
            return await client._request("_mustang.agent/commands/list", {})
    commands = _run(_check()).get("commands", [])
    assert any(command.get("name") == "cron" for command in commands)


def test_loop_skill(phase2_kernel: tuple[int, str, Path, Path]) -> None:
    _assert_tool(
        phase2_kernel,
        "/loop 5m check build status PHASE2_LOOP_SKILL",
        "CronCreate",
        "PHASE2_LOOP_SKILL_OK",
    )
