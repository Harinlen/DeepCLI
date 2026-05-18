"""E2E tests for BashTool compound command safety classification.

Exercises the full kernel authorization path:
  LLM → ToolUseContent("Bash", {"command": ...}) → ToolExecutor →
  ToolAuthorizer.authorize → BashTool.default_risk → decision

Tests verify that compound read-only commands auto-allow and
non-read-only compound commands surface permission requests.
"""

from __future__ import annotations

import asyncio
from typing import Any

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    TurnComplete,
)
from tests.e2e.test_probe_phase2_e2e import phase2_kernel


_TEST_TIMEOUT: float = 15.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(port: int, token: str) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_readonly_compound_auto_allows(
    phase2_kernel: tuple[int, str, Any, Any],
) -> None:
    """A compound command composed of read-only sub-commands should
    NOT trigger a permission request — it should be auto-allowed via
    BashTool.default_risk returning (low, allow)."""
    port, token, workspace, _home = phase2_kernel

    async def _test() -> None:
        saw_permission = False
        text_parts: list[str] = []
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            async for event in client.prompt(
                sid,
                "PHASE2_BASH_READONLY_PIPE",
                timeout=_TEST_TIMEOUT,
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, PermissionRequest):
                    saw_permission = True
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    break
        assert saw_permission is False
        assert "PHASE2_BASH_READONLY_PIPE_OK" in "".join(text_parts)

    _run(_test())


def test_unsafe_compound_asks_permission(
    phase2_kernel: tuple[int, str, Any, Any],
) -> None:
    """A compound command with a non-read-only sub-command should
    trigger a permission request."""
    port, token, workspace, _home = phase2_kernel

    async def _test() -> None:
        got_permission_request = False
        text_parts: list[str] = []
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            async for chunk in client.prompt(
                sid,
                "PHASE2_BASH_UNSAFE_PIPE",
                timeout=_TEST_TIMEOUT,
            ):
                if isinstance(chunk, AgentChunk):
                    text_parts.append(chunk.text)
                elif isinstance(chunk, PermissionRequest):
                    input_data = chunk.tool_input or {}
                    cmd = input_data.get("command", "")
                    if "curl" in cmd:
                        got_permission_request = True
                        await client.reply_permission(chunk.req_id, "deny")
                    else:
                        await client.reply_permission(chunk.req_id, "allow_once")
                elif isinstance(chunk, TurnComplete):
                    break

        assert got_permission_request, (
            "Expected a permission request for compound command with curl, "
            "but none was received — BashTool.default_risk may be too permissive"
        )
        assert "PHASE2_BASH_UNSAFE_PIPE_DENIED_OK" in "".join(text_parts)

    _run(_test())


def test_destructive_warning_in_permission_message(
    phase2_kernel: tuple[int, str, Any, Any],
) -> None:
    """A destructive command should include a warning in the permission
    message (via BashTool.destructive_warning → _build_ask_message)."""
    port, token, workspace, _home = phase2_kernel

    async def _test() -> None:
        got_warning = False
        summaries: list[str] = []
        text_parts: list[str] = []
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session(cwd=str(workspace))
            async for chunk in client.prompt(
                sid,
                "PHASE2_BASH_DESTRUCTIVE_WARNING",
                timeout=_TEST_TIMEOUT,
            ):
                if isinstance(chunk, AgentChunk):
                    text_parts.append(chunk.text)
                elif isinstance(chunk, PermissionRequest):
                    # The input_summary should contain the destructive warning
                    summary = chunk.input_summary or ""
                    summaries.append(summary)
                    if "uncommitted" in summary.lower() or "discard" in summary.lower():
                        got_warning = True
                    await client.reply_permission(chunk.req_id, "deny")
                elif isinstance(chunk, TurnComplete):
                    break

        assert got_warning, (
            "Expected destructive warning in permission message for "
            f"'git reset --hard', but none found. Summaries: {summaries!r}"
        )
        assert "PHASE2_BASH_DESTRUCTIVE_WARNING_DENIED_OK" in "".join(text_parts)

    _run(_test())
