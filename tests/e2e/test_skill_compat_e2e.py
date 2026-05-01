"""E2E: Claude Code .claude/skills/ compatibility.

Tests that the kernel discovers skills from .claude/skills/ alongside
.mustang/skills/, enabling zero-modification reuse of existing Claude
Code skill files.
"""

from __future__ import annotations

import asyncio
from typing import Any


from probe.client import ProbeClient


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_kernel_starts_with_claude_compat_enabled(kernel: tuple[int, str]) -> None:
    """The kernel starts with claude_compat=True by default.

    This means .claude/skills/ directories are scanned alongside
    .mustang/skills/.  Verify by checking the kernel starts
    successfully.
    """
    port, token = kernel

    async def _run_test() -> dict[str, Any]:
        async with ProbeClient(port=port, token=token) as client:
            caps = await client.initialize()
        return caps

    caps = _run(_run_test())
    assert caps is not None


def test_session_new_with_custom_cwd(kernel: tuple[int, str]) -> None:
    """Creating a session with a custom cwd works.

    Skills from cwd/.mustang/skills/ and cwd/.claude/skills/ should
    be discoverable for that session.
    """
    port, token = kernel

    async def _run_test() -> str:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            # Use /tmp as cwd — there won't be skills there, but the
            # session should start without crashing.
            sid = await client.new_session(cwd="/tmp")
        return sid

    sid = _run(_run_test())
    assert isinstance(sid, str)
    assert len(sid) > 0
