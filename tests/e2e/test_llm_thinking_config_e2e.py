"""E2E probe for kernel-wide LLM thinking configuration."""

from __future__ import annotations

import asyncio
from typing import Any

from probe.client import ProbeClient

_TEST_TIMEOUT = 30.0


def _run(coro: Any) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=_TEST_TIMEOUT)

    return asyncio.run(_guarded())


def test_llm_thinking_toggle_routes_to_primary_runtime(kernel: tuple[int, str]) -> None:
    """The Access -> Hub -> Primary Runtime path persists thinking changes."""
    port, token = kernel

    async def _check() -> tuple[bool, bool, bool]:
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            await client.initialize()
            await client._request("_mustang.agent/llm/thinking_set", {"enabled": False})
            initial = await client._request("_mustang.agent/llm/thinking_get", {})
            enabled = await client._request(
                "_mustang.agent/llm/thinking_set",
                {"enabled": True},
            )
            disabled = await client._request(
                "_mustang.agent/llm/thinking_set",
                {"enabled": False},
            )
            final = await client._request("_mustang.agent/llm/thinking_get", {})
        return (
            bool(initial["enabled"]),
            bool(enabled["enabled"]),
            bool(disabled["enabled"] or final["enabled"]),
        )

    initial_enabled, enabled_after_set, still_enabled_after_disable = _run(_check())

    assert initial_enabled is False
    assert enabled_after_set is True
    assert still_enabled_after_disable is False
