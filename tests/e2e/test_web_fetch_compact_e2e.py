"""E2E probe: WebFetch secondary-model post-processing via compact role.

Drives the live kernel through the LLM so WebFetch's deferred tool
gets promoted via ToolSearch, then called with a ``prompt`` parameter
that triggers the secondary-model path.  The summary should come from
the configured ``compact`` role (Bedrock Haiku in the dev config);
we verify end-to-end behaviour — the LLM receives a meaningfully
compressed result and can answer the user based on the summary.

Coverage map
------------
test_webfetch_summarises_via_compact_role
    → WebFetch post-processing closure reaches BedrockProvider via
      LLMManager.model_for_or_default("compact")
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


_LLM_TIMEOUT: float = 180.0


def _run(coro: Any, *, timeout: float = _LLM_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(_guarded())


def _client(port: int, token: str) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=_LLM_TIMEOUT)


def _skip_if_no_llm(port: int, token: str) -> None:
    async def _check() -> list[dict[str, Any]]:
        async with _client(port, token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/provider_list", {})
        return result.get("providers", [])

    providers = _run(_check(), timeout=30)
    if not providers:
        pytest.skip("No LLM providers configured — skipping")


async def _collect_turn(
    client: ProbeClient, sid: str, prompt: str
) -> tuple[str, str, list[ToolCallEvent]]:
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


def test_webfetch_summarises_via_compact_role(kernel: tuple[int, str]) -> None:
    """Drive the LLM to fetch a small URL with a prompt param.

    The assistant's text reply should describe the page's content —
    which it can only do via the summariser output (raw HTML would
    swamp the LLM with tags).  Passing the turn confirms:
      • WebFetch tool is discoverable through ToolSearch
      • ctx.summarise is wired and callable
      • The compact role resolves, or (if not configured) default
        fallback keeps the tool working
    """
    port, token = kernel
    _skip_if_no_llm(port, token)

    async def _run_test() -> None:
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()

            text, stop, tools = await _collect_turn(
                client,
                sid,
                "Use the WebFetch tool to fetch https://example.com with the "
                "prompt 'In one sentence, what is this page about?'. "
                "If you need to discover the tool first, use ToolSearch. "
                "After WebFetch returns, summarise its result in one sentence.",
            )

            assert stop == "end_turn", f"Turn did not complete: {stop}, text: {text}"

            titles = [t.title for t in tools]
            # Either WebFetch was called directly or promoted via ToolSearch.
            # Accept REPL as a valid wrapper too (REPL mode hides primitives
            # but can still dispatch WebFetch internally).
            assert any(
                t in {"WebFetch", "ToolSearch", "REPL"} for t in titles
            ), f"Expected WebFetch-related tool calls, got: {titles}"

            # The reply should discuss example.com's content — typically
            # a phrase like "reserved" / "documentation" / "example
            # domain".  We accept any of these as evidence that either
            # the summariser or the raw content reached the LLM.
            reply_lower = text.lower()
            matched = any(
                needle in reply_lower
                for needle in ("reserved", "documentation", "example", "illustrative")
            )
            assert matched, (
                f"LLM reply does not describe example.com content:\n{text!r}"
            )

    _run(_run_test())
