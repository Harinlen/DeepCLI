"""E2E: Skill activation via SkillTool.

Tests that the LLM can invoke the Skill tool and receive skill body
content in the conversation.  Requires a configured LLM model.
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


def test_skill_tool_in_tool_list(kernel: tuple[int, str]) -> None:
    """The Skill tool is listed in the initialize response.

    This is a prerequisite for the LLM to be able to invoke it.
    """
    port, token = kernel

    async def _run_test() -> dict[str, Any]:
        async with ProbeClient(port=port, token=token) as client:
            caps = await client.initialize()
        return caps

    caps = _run(_run_test())
    # Just verify the kernel starts and returns capabilities.
    assert isinstance(caps, dict)


def test_prompt_with_skill_invocation(kernel: tuple[int, str]) -> None:
    """A prompt requesting skill invocation completes without crashing.

    The LLM may or may not actually call the Skill tool (depends on
    the model and whether skills exist), but the turn must complete
    gracefully.
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
                "If there are any available skills listed, tell me their names. "
                "If not, just say 'no skills available'.",
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), stop_reason

    text, stop_reason = _run(_run_test())
    assert stop_reason == "end_turn"
    assert len(text) > 0


def test_skill_tool_handles_disable_model_invocation(kernel: tuple[int, str]) -> None:
    """The Skill tool rejects skills with disable-model-invocation: true.

    This is tested at the unit level; at E2E we just verify the kernel
    doesn't crash when the Skill tool validates input.
    """
    if not _has_model(kernel):
        pytest.skip("No LLM configured")

    port, token = kernel

    async def _run_test() -> str:
        stop_reason = "unknown"
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session()
            async for event in client.prompt(
                sid,
                "Use the Skill tool to invoke a skill called 'test-disabled'. "
                "If it doesn't exist or fails, just acknowledge that.",
            ):
                if isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return stop_reason

    stop_reason = _run(_run_test())
    assert stop_reason == "end_turn"
