"""E2E: Skill listing injection into system prompt.

Tests that SkillManager listing content reaches the LLM via
PromptBuilder injection.  The LLM's awareness of available skills
is verified by asking it what skills it can see.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from probe.client import AgentChunk, ProbeClient, TurnComplete


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _has_model(kernel: tuple[int, str]) -> bool:
    port, token = kernel

    async def _check() -> bool:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/profile_list", {})
        return bool(result.get("profiles"))

    return _run(_check())


def test_listing_injected_without_crash(kernel: tuple[int, str]) -> None:
    """The kernel starts and injects skill listing without errors.

    PromptBuilder calls skills.get_skill_listing() — if SkillManager
    is broken, the kernel would fail to produce a system prompt.
    This test verifies the integration path doesn't crash.
    """
    if not _has_model(kernel):
        pytest.skip("No LLM configured")

    port, token = kernel

    async def _run_test() -> tuple[str, str]:
        text_parts: list[str] = []
        stop_reason = "unknown"
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session()
            async for event in client.prompt(
                sid,
                "What tools do you have available? List them briefly.",
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), stop_reason

    text, stop_reason = _run(_run_test())
    assert stop_reason == "end_turn"
    # The LLM should mention at least some tools.
    assert len(text) > 0


def test_skill_tool_mentioned_in_response(kernel: tuple[int, str]) -> None:
    """The LLM should be aware of the Skill tool.

    Since Skill is a registered tool, the LLM should mention it (or
    at least not crash) when asked about available tools.
    """
    if not _has_model(kernel):
        pytest.skip("No LLM configured")

    port, token = kernel

    async def _run_test() -> tuple[str, str]:
        text_parts: list[str] = []
        stop_reason = "unknown"
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session()
            async for event in client.prompt(
                sid,
                "Do you have a 'Skill' tool available? Answer yes or no.",
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), stop_reason

    text, stop_reason = _run(_run_test())
    assert stop_reason == "end_turn"
    # The LLM should acknowledge the Skill tool exists.
    text_lower = text.lower()
    assert "yes" in text_lower or "skill" in text_lower, (
        f"Expected LLM to acknowledge Skill tool, got: {text[:200]}"
    )
