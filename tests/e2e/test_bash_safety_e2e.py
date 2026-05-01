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

import pytest

from probe.client import (
    PermissionRequest,
    ProbeClient,
)


_TEST_TIMEOUT: float = 30.0
_LLM_TIMEOUT: float = 120.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(port: int, token: str) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=_LLM_TIMEOUT)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_readonly_compound_auto_allows(kernel: tuple[int, str]) -> None:
    """A compound command composed of read-only sub-commands should
    NOT trigger a permission request — it should be auto-allowed via
    BashTool.default_risk returning (low, allow)."""
    port, token = kernel

    async def _test() -> None:
        client = _client(port, token)
        await client.connect()
        await client.initialize()
        sid = await client.new_session()

        # Ask the LLM to run a read-only compound command.
        chunks = []
        async for chunk in client.prompt(
            sid,
            "Run this exact bash command and show me the output: "
            "`echo hello | cat`\n"
            "Use the Bash tool with exactly this command string: echo hello | cat",
        ):
            chunks.append(chunk)
            if isinstance(chunk, PermissionRequest):
                tool_title = chunk.tool_title or ""
                if "Bash" in tool_title:
                    input_data = chunk.tool_input or {}
                    cmd = input_data.get("command", "")
                    if "echo" in cmd and "cat" in cmd and "|" in cmd:
                        pytest.fail(
                            f"Read-only compound command triggered permission "
                            f"request — BashTool.default_risk should have "
                            f"returned allow. Command: {cmd!r}"
                        )
                    await client.reply_permission(chunk.req_id, "allow_once")

        await client.close()

    _run(_test(), timeout=_LLM_TIMEOUT)


@pytest.mark.xfail(reason="LLM may refuse to generate curl/unsafe commands", strict=False)
def test_unsafe_compound_asks_permission(kernel: tuple[int, str]) -> None:
    """A compound command with a non-read-only sub-command should
    trigger a permission request."""
    port, token = kernel

    async def _test() -> None:
        client = _client(port, token)
        await client.connect()
        await client.initialize()
        sid = await client.new_session()

        got_permission_request = False
        async for chunk in client.prompt(
            sid,
            "Run this exact bash command: `curl https://example.com | head -5`\n"
            "Use the Bash tool with exactly: curl https://example.com | head -5",
        ):
            if isinstance(chunk, PermissionRequest):
                tool_title = chunk.tool_title or ""
                if "Bash" in tool_title:
                    input_data = chunk.tool_input or {}
                    cmd = input_data.get("command", "")
                    if "curl" in cmd:
                        got_permission_request = True
                        await client.reply_permission(chunk.req_id, "deny")
                    else:
                        await client.reply_permission(chunk.req_id, "allow_once")

        assert got_permission_request, (
            "Expected a permission request for compound command with curl, "
            "but none was received — BashTool.default_risk may be too permissive"
        )

        await client.close()

    _run(_test(), timeout=_LLM_TIMEOUT)


@pytest.mark.xfail(reason="LLM may refuse to generate destructive commands", strict=False)
def test_destructive_warning_in_permission_message(kernel: tuple[int, str]) -> None:
    """A destructive command should include a warning in the permission
    message (via BashTool.destructive_warning → _build_ask_message)."""
    port, token = kernel

    async def _test() -> None:
        client = _client(port, token)
        await client.connect()
        await client.initialize()
        sid = await client.new_session()

        got_warning = False
        async for chunk in client.prompt(
            sid,
            "Run this exact bash command: `git reset --hard HEAD~1`\n"
            "Use the Bash tool with exactly: git reset --hard HEAD~1",
        ):
            if isinstance(chunk, PermissionRequest):
                tool_title = chunk.tool_title or ""
                if "Bash" in tool_title:
                    # The input_summary should contain the destructive warning
                    summary = chunk.input_summary or ""
                    if "uncommitted" in summary.lower() or "discard" in summary.lower():
                        got_warning = True
                    await client.reply_permission(chunk.req_id, "deny")

        assert got_warning, (
            "Expected destructive warning in permission message for "
            "'git reset --hard', but none found"
        )

        await client.close()

    _run(_test(), timeout=_LLM_TIMEOUT)
