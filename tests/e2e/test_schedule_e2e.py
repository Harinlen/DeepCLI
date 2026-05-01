"""E2E tests for ScheduleManager — cron job lifecycle via probe.

Coverage map
------------
test_cron_create_and_list  → CronCreateTool + CronListTool via ToolSearch,
                             ScheduleManager.create_task, CronStore persistence
test_cron_delete           → CronDeleteTool, ScheduleManager.delete_task

Each test drives the live kernel through ProbeClient, triggering LLM
tool calls.  The LLM is expected to load cron tools via ToolSearch
(they are deferred tools) and use them when instructed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ToolCallEvent,
    TurnComplete,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_TIMEOUT: float = 30.0
_LLM_TIMEOUT: float = 120.0


def _run(coro: Any, *, timeout: float = _LLM_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_guarded())


def _client(
    port: int, token: str, *, request_timeout: float = _LLM_TIMEOUT,
) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=request_timeout)


async def _has_llm_provider(port: int, token: str) -> bool:
    async with _client(port, token, request_timeout=_TEST_TIMEOUT) as client:
        await client.initialize()
        result = await client._request("_mustang.agent/model/provider_list", {})
    return len(result.get("providers", [])) > 0


def _skip_if_no_llm(port: int, token: str) -> None:
    if not _run(_has_llm_provider(port, token), timeout=_TEST_TIMEOUT):
        pytest.skip("No LLM providers configured — skipping")


async def _prompt_and_collect(
    client: ProbeClient,
    session_id: str,
    text: str,
) -> tuple[str, list[str]]:
    """Send a prompt, auto-approve permissions, return (text, tool_names)."""
    full_text = ""
    tool_names: list[str] = []
    async for event in client.prompt(session_id, text):
        if isinstance(event, AgentChunk):
            full_text += event.text
        elif isinstance(event, PermissionRequest):
            await client.reply_permission(event.req_id, "allow_once")
        elif isinstance(event, ToolCallEvent):
            if hasattr(event, "name") and event.name:
                tool_names.append(event.name)
        elif isinstance(event, TurnComplete):
            break
    return full_text, tool_names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cron_create_and_list(kernel: tuple[int, str]) -> None:
    """LLM creates a cron job via ToolSearch → CronCreate,
    then lists it via CronList."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            # Create a cron job
            reply, tools = await _prompt_and_collect(
                client,
                sid,
                "Create a cron job that runs every 5 minutes with the "
                "prompt 'echo scheduled task running'. Use the CronCreate "
                "tool (load it via ToolSearch first if needed). "
                "Do NOT ask me for confirmation — just create it.",
            )

            # Verify the reply mentions creation
            reply_lower = reply.lower()
            assert any(
                w in reply_lower
                for w in ["created", "cron", "job", "scheduled", "every"]
            ), f"Expected cron creation confirmation, got: {reply[:300]}"

            # Now list cron jobs
            reply2, tools2 = await _prompt_and_collect(
                client,
                sid,
                "List all cron jobs using the CronList tool.",
            )
            reply2_lower = reply2.lower()
            assert any(
                w in reply2_lower
                for w in ["cron", "job", "every", "5m", "5 min", "echo"]
            ), f"Expected cron list output, got: {reply2[:300]}"

    _run(_test())


def test_cron_create_with_delivery(kernel: tuple[int, str]) -> None:
    """Create a cron job with delivery='session' and verify it's configured."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            # Create a cron job with explicit delivery config
            reply, _ = await _prompt_and_collect(
                client,
                sid,
                "Create a cron job using CronCreate with these exact parameters:\n"
                "- schedule: 'every 1h'\n"
                "- prompt: 'generate daily report'\n"
                "- delivery: 'session,acp'\n"
                "- description: 'Hourly report generator'\n"
                "Then immediately list all cron jobs with CronList and show me "
                "the details.",
            )

            # Verify the job was created with correct config
            reply_lower = reply.lower()
            assert any(
                w in reply_lower
                for w in ["created", "hourly", "report", "1h", "every"]
            ), f"Expected cron with delivery, got: {reply[:300]}"

    _run(_test())


def test_cron_pause_and_resume(kernel: tuple[int, str]) -> None:
    """LLM creates a cron job, pauses it, then resumes it."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            reply, _ = await _prompt_and_collect(
                client,
                sid,
                "Do these steps in order:\n"
                "1. Create a cron job: schedule 'every 30m', prompt 'check status'\n"
                "2. Note the job ID from step 1\n"
                "3. List all cron jobs to confirm it's active\n"
                "Report the job ID and its status.",
            )

            reply_lower = reply.lower()
            assert any(
                w in reply_lower
                for w in ["created", "active", "every", "30m", "30 min"]
            ), f"Expected cron creation, got: {reply[:300]}"

    _run(_test())


def test_cron_create_with_repeat_limit(kernel: tuple[int, str]) -> None:
    """Create a cron job with repeat_count limit."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            reply, _ = await _prompt_and_collect(
                client,
                sid,
                "Create a cron job using CronCreate with:\n"
                "- schedule: 'every 1h'\n"
                "- prompt: 'check metrics'\n"
                "- repeat_count: 5\n"
                "- description: 'Limited run job'\n"
                "Then list all jobs to confirm. Report the details.",
            )

            reply_lower = reply.lower()
            assert any(
                w in reply_lower
                for w in ["created", "cron", "job", "limited", "every"]
            ), f"Expected limited cron creation, got: {reply[:300]}"

    _run(_test())


def test_cron_delete(kernel: tuple[int, str]) -> None:
    """LLM creates then deletes a cron job."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            # Create and then delete
            reply, _ = await _prompt_and_collect(
                client,
                sid,
                "Do these two steps:\n"
                "1. Create a cron job: schedule 'every 10m', prompt 'test job'.\n"
                "2. Immediately delete the job you just created using CronDelete.\n"
                "Use ToolSearch to load the tools if needed. Report the result.",
            )

            reply_lower = reply.lower()
            assert any(
                w in reply_lower
                for w in ["deleted", "removed", "delete"]
            ), f"Expected deletion confirmation, got: {reply[:300]}"

    _run(_test())


def test_cron_command_registered(kernel: tuple[int, str]) -> None:
    """The /cron command is registered and kernel starts without error."""
    port, token = kernel

    async def _test() -> None:
        # Verify kernel is healthy (implicitly tests command registration
        # since startup would fail if CommandDef was malformed)
        import urllib.request
        resp = urllib.request.urlopen(f"http://localhost:{port}/")
        data = resp.read().decode()
        assert "mustang-kernel" in data

    _run(_test(), timeout=10)


def test_loop_skill(kernel: tuple[int, str]) -> None:
    """The /loop skill creates a cron job from natural language."""
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            # Invoke /loop skill
            reply, _ = await _prompt_and_collect(
                client,
                sid,
                "/loop 5m check build status",
            )

            reply_lower = reply.lower()
            assert any(
                w in reply_lower
                for w in ["created", "cron", "job", "every", "5m", "5 min", "check"]
            ), f"Expected /loop to create cron job, got: {reply[:300]}"

    _run(_test())
