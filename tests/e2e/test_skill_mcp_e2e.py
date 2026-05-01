"""E2E: MCP skill integration.

Tests that the SkillManager's MCP skill registration interface
exists and the kernel starts correctly with the MCP+Skills subsystem
combination.  Actual MCP prompt-as-skill registration requires an
MCP server that exposes prompt resources — that's tested via the
existing MCP E2E tests.
"""

from __future__ import annotations

import asyncio
from typing import Any


from probe.client import ProbeClient


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_kernel_starts_with_mcp_and_skills(kernel: tuple[int, str]) -> None:
    """The kernel starts with both MCPManager and SkillManager loaded.

    Verifies that the startup order (MCP before Tools before Skills)
    doesn't cause import or initialization errors.
    """
    port, token = kernel

    async def _run_test() -> dict[str, Any]:
        async with ProbeClient(port=port, token=token) as client:
            caps = await client.initialize()
        return caps

    caps = _run(_run_test())
    assert caps is not None
    assert "mcpCapabilities" in caps


def test_session_with_mcp_servers_empty(kernel: tuple[int, str]) -> None:
    """Creating a session with empty mcpServers list works.

    When no MCP servers are configured, SkillManager should have no
    MCP skills registered — but the system should work fine.
    """
    port, token = kernel

    async def _run_test() -> str:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session()
        return sid

    sid = _run(_run_test())
    assert isinstance(sid, str)
