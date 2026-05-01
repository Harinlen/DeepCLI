"""E2E test: session resume preserves LLM conversation history.

Verifies gap #2 fix — after resuming a session, the orchestrator has
the full conversation history so the LLM can reference prior context.

Test plan:
1. Create a new session
2. Send a prompt with a unique fact (e.g. "My favorite color is chartreuse")
3. Disconnect
4. Load the session on a new connection
5. Send a follow-up prompt asking about the fact
6. Assert the LLM's response references the prior context

This test requires an LLM provider to be configured and reachable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    TurnComplete,
)


_LLM_TIMEOUT: float = 120.0
_TEST_TIMEOUT: float = 30.0


def _client(port: int, token: str, *, request_timeout: float = _TEST_TIMEOUT) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=request_timeout)


async def _collect_text(
    client: ProbeClient,
    session_id: str,
    text: str,
    *,
    skip_on_empty: bool = True,
) -> str:
    """Send a prompt and collect all agent text from the response."""
    chunks: list[str] = []
    all_events: list[Any] = []
    async for event in client.prompt(session_id, text, timeout=_LLM_TIMEOUT):
        all_events.append(event)
        if isinstance(event, AgentChunk):
            chunks.append(event.text)
        elif isinstance(event, PermissionRequest):
            await client.reply_permission(event.req_id, "allow_once")
        elif isinstance(event, TurnComplete):
            if event.error and skip_on_empty:
                pytest.skip(f"LLM turn failed: {event.error}")
            break
    if not chunks and skip_on_empty:
        event_summary = [f"{type(e).__name__}" for e in all_events[:10]]
        pytest.skip(
            f"LLM returned no text (probably not configured). "
            f"Events: {event_summary}"
        )
    return "".join(chunks)


def test_session_resume_preserves_context(kernel: tuple[int, str]) -> None:
    """After resume, the LLM can reference information from a prior turn.

    E2E path: new_session → prompt (establish context) → disconnect →
    load_session → prompt (ask about prior context) → assert LLM remembers.
    """
    port, token = kernel

    def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
        async def _guarded() -> Any:
            return await asyncio.wait_for(coro, timeout=timeout)
        return asyncio.run(_guarded())

    async def _run_test() -> None:
        # --- Phase 1: Create session and establish context ---
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
            await client.initialize()
            sid = await client.new_session()

            response1 = await _collect_text(
                client,
                sid,
                "Remember this exactly: my favorite color is chartreuse. "
                "Just acknowledge that you've noted it, nothing else.",
            )
            assert response1, "LLM returned empty response in phase 1"

        # --- Phase 2: Resume session on a new connection and verify context ---
        async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client2:
            await client2.initialize()
            history = await client2.load_session(sid)
            assert len(history) > 0, "No history events replayed"

            response2 = await _collect_text(
                client2,
                sid,
                "What is my favorite color? "
                "Reply with just the color name, nothing else.",
            )
            assert "chartreuse" in response2.lower(), (
                f"LLM did not reference prior context. Response: {response2!r}"
            )

    _run(_run_test(), timeout=_LLM_TIMEOUT * 2)
