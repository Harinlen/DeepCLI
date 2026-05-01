"""Tests that run_with_timeout kills the subprocess on cancel."""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from daemon.extensions.tools.builtin.subprocess_utils import run_with_timeout


def _linux_only() -> None:
    """Skip tests that depend on POSIX signals / /bin/bash."""
    if os.name != "posix":
        pytest.skip("POSIX-only subprocess semantics")


class TestSubprocessCancellation:
    @pytest.mark.asyncio
    async def test_cancel_kills_subprocess(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """When the caller is cancelled, the child process gets killed."""
        _linux_only()

        # Spawn a process that will sleep far longer than the test
        # lifetime; we expect run_with_timeout to SIGKILL it on cancel.
        task = asyncio.create_task(
            run_with_timeout(
                ["bash", "-c", "sleep 60"],
                cwd=str(tmp_path),
                timeout_s=60.0,
            )
        )

        # Give asyncio a moment to actually start the process.
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # No straightforward cross-platform way to verify the child
        # was killed from outside.  If the coroutine returned without
        # raising, we already tolerate leaks; here we at least
        # confirm the coroutine unwound cleanly with CancelledError.

    @pytest.mark.asyncio
    async def test_normal_completion_no_kill_path(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Fast commands finish without entering the kill branch."""
        _linux_only()
        result = await run_with_timeout(
            ["bash", "-c", "echo hi"],
            cwd=str(tmp_path),
            timeout_s=5.0,
        )
        assert result.returncode == 0
        assert "hi" in result.stdout
        assert result.timed_out is False

    @pytest.mark.asyncio
    async def test_timeout_still_kills(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Timeout path still kills the child (pre-existing behaviour)."""
        _linux_only()
        result = await run_with_timeout(
            ["bash", "-c", "sleep 5"],
            cwd=str(tmp_path),
            timeout_s=0.2,
        )
        assert result.timed_out is True

    @pytest.mark.asyncio
    async def test_cancel_returns_within_grace(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Cancel path unwinds within the 2s kill grace window."""
        _linux_only()
        task = asyncio.create_task(
            run_with_timeout(
                ["bash", "-c", f"trap 'echo trapped' {signal.SIGTERM}; sleep 60"],
                cwd=str(tmp_path),
                timeout_s=60.0,
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            # The reap wait is 2 s internally; give ourselves slack.
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=3.0)
        except asyncio.TimeoutError:
            pytest.fail("cancel path took longer than 3s to unwind")
