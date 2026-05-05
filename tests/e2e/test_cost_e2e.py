"""E2E coverage for the DeepCLI `/cost` usage ACP surface."""

from __future__ import annotations

import asyncio
from typing import Any

from probe.client import ProbeClient

_TEST_TIMEOUT = 30.0


def _run(coro: Any) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=_TEST_TIMEOUT)

    return asyncio.run(_guarded())


def test_session_get_usage_live_kernel(kernel: tuple[int, str]) -> None:
    """Drive `_mustang.agent/session/get_usage` through a real kernel process."""
    port, token = kernel

    async def _run_test() -> dict[str, Any]:
        async with ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT) as client:
            await client.initialize()
            session_id = await client.new_session()
            return await client._request(
                "_mustang.agent/session/get_usage",
                {"sessionId": session_id},
            )

    result = _run(_run_test())

    assert result["sessionId"]
    assert result["tokens"]["total"] == 0
    assert result["context"]["totalTokens"] == 0
    assert [section["id"] for section in result["context"]["sections"]] == [
        "system_prompt",
        "memory",
        "conversation",
        "tools",
    ]
    assert result["history"]["turns"] == 0
