"""Shared subprocess helpers for tools that shell out.

Extracted from bash and grep to deduplicate the timeout + kill pattern.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Grace period between sending SIGKILL to a subprocess and giving up
# on waiting for it to reap.  If the child keeps itself alive past
# this window we log + move on; we are in an unwinding path and
# cannot block the caller indefinitely.
_KILL_WAIT_S = 2.0


@dataclass
class SubprocessResult:
    """Result of running a subprocess with timeout."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


async def run_with_timeout(
    cmd: list[str],
    *,
    cwd: str,
    timeout_s: float,
    env: dict[str, str] | None = None,
    stdin_bytes: bytes | None = None,
) -> SubprocessResult:
    """Run a subprocess with a timeout, killing on expiry.

    Args:
        cmd: Command and arguments for ``create_subprocess_exec``.
        cwd: Working directory for the process.
        timeout_s: Timeout in seconds.
        env: Optional environment dict.  ``None`` inherits from parent.
        stdin_bytes: Optional bytes to send to the child's stdin.

    Returns:
        SubprocessResult with decoded output and exit info.

    Raises:
        OSError: If the process cannot be started.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        await _kill_and_reap(proc)
        # Drain any remaining output after kill
        try:
            stdout_bytes, stderr_bytes = await proc.communicate()
        except Exception:  # noqa: BLE001
            stdout_bytes, stderr_bytes = b"", b""
        return SubprocessResult(
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            returncode=proc.returncode if proc.returncode is not None else -1,
            timed_out=True,
        )
    except asyncio.CancelledError:
        # The orchestrator is unwinding — kill the subprocess so we
        # do not leave a zombie, then re-raise.  The cancel-finalizer
        # upstream will write a synthetic <cancelled> tool result.
        await _kill_and_reap(proc)
        raise

    return SubprocessResult(
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        returncode=proc.returncode if proc.returncode is not None else -1,
    )


async def _kill_and_reap(proc: asyncio.subprocess.Process) -> None:
    """Send SIGKILL to *proc* and await its reap with a short grace.

    Swallows common races: the process may have exited already
    (``ProcessLookupError``) or may linger past the grace period
    (in which case we log and move on — we cannot block forever
    on an unwinding path).
    """
    try:
        proc.kill()
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_WAIT_S)
    except asyncio.TimeoutError:
        logger.warning("Subprocess pid=%s ignored SIGKILL for %ss", proc.pid, _KILL_WAIT_S)
