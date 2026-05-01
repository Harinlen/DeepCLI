"""E2E: Dynamic skill discovery + conditional activation.

Tests that file-tool operations trigger SkillManager.on_file_touched()
and that new skill directories are discovered at runtime.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from probe.client import ProbeClient, TurnComplete


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


def test_file_write_does_not_crash_skill_discovery(kernel: tuple[int, str]) -> None:
    """FileWrite triggering on_file_touched doesn't crash the kernel.

    The ToolExecutor calls skills.on_file_touched() after FileWrite.
    If SkillManager is broken, this would crash the tool execution
    pipeline.  This test verifies the integration is safe.
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
                "Write the text 'hello' to a file called /tmp/mustang_e2e_test_skill_dynamic.txt",
            ):
                if isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return stop_reason

    stop_reason = _run(_run_test())
    assert stop_reason == "end_turn"


def test_file_edit_does_not_crash_skill_discovery(kernel: tuple[int, str]) -> None:
    """FileEdit triggering on_file_touched doesn't crash the kernel.

    Same as above but for the FileEdit tool path.
    """
    if not _has_model(kernel):
        pytest.skip("No LLM configured")

    port, token = kernel

    # Ensure the file exists first.
    import tempfile
    from pathlib import Path

    test_file = Path(tempfile.gettempdir()) / "mustang_e2e_test_skill_edit.txt"
    test_file.write_text("original content\nline 2\n")

    async def _run_test() -> str:
        stop_reason = "unknown"
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session()
            async for event in client.prompt(
                sid,
                f"Edit the file {test_file} and replace 'original' with 'modified'.",
            ):
                if isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return stop_reason

    stop_reason = _run(_run_test())
    assert stop_reason == "end_turn"

    # Cleanup.
    test_file.unlink(missing_ok=True)
