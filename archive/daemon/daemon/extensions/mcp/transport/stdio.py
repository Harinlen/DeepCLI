"""Stdio transport — MCP over subprocess stdin/stdout.

Wraps the existing LSP-style Content-Length framing primitives from
the original ``stdio_transport`` module into a :class:`Transport`
implementation.  The subprocess lifecycle (spawn, drain stderr,
graceful kill) is fully encapsulated here — :class:`McpClient` only
sees the ``connect / send / receive / close`` interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from daemon.errors import McpError
from daemon.extensions.mcp.config import McpServerEntry
from daemon.extensions.mcp.transport.base import Transport, TransportClosed

logger = logging.getLogger(__name__)

# Stderr buffer cap — prevents unbounded memory on noisy servers.
MAX_STDERR_BYTES = 1 * 1024 * 1024  # 1 MB

# Grace period before forcibly killing the subprocess.
_CLOSE_TIMEOUT = 5.0


class StdioTransport(Transport):
    """MCP transport over subprocess stdin/stdout with LSP framing.

    Manages the full subprocess lifecycle: spawn on ``connect()``,
    Content-Length framing on ``send()``/``receive()``, and graceful
    kill on ``close()``.  Stderr is drained into an internal buffer
    (capped at 1 MB) for diagnostics on failure.

    Args:
        entry: Server configuration (command, args, env).
    """

    def __init__(self, entry: McpServerEntry) -> None:
        self._entry = entry
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_buf: bytearray = bytearray()
        self._stderr_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Transport interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Spawn the MCP server subprocess and begin draining stderr.

        Raises:
            McpError: If the subprocess cannot be launched.
        """
        self._stderr_buf.clear()
        self._process = await _spawn_subprocess(self._entry)

        if self._process.stderr:
            self._stderr_task = asyncio.create_task(
                _drain_stderr(self._process.stderr, self._stderr_buf),
                name=f"mcp-stderr-{self._entry.name}",
            )

        logger.debug(
            "Stdio transport connected for '%s' (pid %s)",
            self._entry.name,
            self._process.pid,
        )

    async def send(self, message: bytes) -> None:
        """Write a Content-Length-framed message to subprocess stdin.

        Args:
            message: Serialized JSON-RPC payload.

        Raises:
            TransportClosed: If stdin is not writable.
        """
        proc = self._process
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise TransportClosed("Stdio transport not connected")

        header = f"Content-Length: {len(message)}\r\n\r\n".encode()
        proc.stdin.write(header + message)
        await proc.stdin.drain()

    async def receive(self) -> bytes:
        """Read one Content-Length-framed message from subprocess stdout.

        Blocks until a complete message arrives.

        Returns:
            Raw JSON-RPC message bytes.

        Raises:
            TransportClosed: On EOF or incomplete read.
        """
        proc = self._process
        if proc is None or proc.stdout is None:
            raise TransportClosed("Stdio transport not connected")

        try:
            content_length = await _read_content_length(proc.stdout)
        except asyncio.CancelledError:
            raise TransportClosed("Read cancelled") from None

        if content_length is None:
            raise TransportClosed("Subprocess stdout EOF")

        try:
            return await proc.stdout.readexactly(content_length)
        except asyncio.IncompleteReadError as exc:
            logger.warning(
                "MCP stdio: incomplete read (expected %d bytes, got %d) — "
                "transport is now corrupted and will be closed",
                content_length,
                len(exc.partial),
            )
            raise TransportClosed(
                f"Incomplete message body ({len(exc.partial)}/{content_length} bytes)"
            ) from None
        except asyncio.CancelledError:
            raise TransportClosed("Read cancelled") from None

    async def close(self) -> None:
        """Gracefully shut down the subprocess.  Idempotent.

        Closes stdin, waits briefly for exit, then kills if needed.
        Cancels the stderr drain task.
        """
        # Cancel stderr drain
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

        # Terminate subprocess
        proc = self._process
        if proc is not None:
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout=_CLOSE_TIMEOUT)
            except (asyncio.TimeoutError, ProcessLookupError):
                proc.kill()
                try:
                    await proc.wait()
                except ProcessLookupError:
                    pass
            self._process = None

        logger.debug("Stdio transport closed for '%s'", self._entry.name)

    @property
    def is_connected(self) -> bool:
        """True if the subprocess is running (no returncode yet)."""
        return self._process is not None and self._process.returncode is None

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def stderr_tail(self) -> str:
        """Last 500 bytes of stderr, for error reporting."""
        if not self._stderr_buf:
            return "(empty)"
        return self._stderr_buf[-500:].decode(errors="replace")


# ------------------------------------------------------------------
# Private helpers (stateless, unit-testable)
# ------------------------------------------------------------------


async def _spawn_subprocess(entry: McpServerEntry) -> asyncio.subprocess.Process:
    """Start an MCP server subprocess with stdin/stdout/stderr pipes.

    Args:
        entry: Server configuration (command, args, env).

    Returns:
        The spawned process.

    Raises:
        McpError: If the subprocess cannot be launched.
    """
    import os

    env = {**os.environ, **entry.env}
    try:
        process = await asyncio.create_subprocess_exec(
            entry.command,
            *entry.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise McpError(f"Failed to start MCP server '{entry.name}': {exc}") from exc

    logger.debug(
        "Started MCP server '%s': %s %s (pid %s)",
        entry.name,
        entry.command,
        " ".join(entry.args),
        process.pid,
    )
    return process


async def _drain_stderr(stream: asyncio.StreamReader, buf: bytearray) -> None:
    """Accumulate subprocess stderr into *buf*, capped at 1 MB.

    Runs until the stream closes or the task is cancelled.
    """
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            remaining = MAX_STDERR_BYTES - len(buf)
            if remaining > 0:
                buf.extend(chunk[:remaining])
    except (asyncio.CancelledError, OSError):
        pass


async def _read_content_length(reader: asyncio.StreamReader) -> int | None:
    """Parse the LSP-style header block and return the body length.

    Returns:
        Byte count of the upcoming body, or ``None`` on EOF.

    Raises:
        McpError: If the header block ends without a Content-Length.
    """
    content_length = -1

    while True:
        line = await reader.readline()
        if not line:
            return None  # EOF
        decoded = line.decode().strip()
        if not decoded:
            break  # End of headers
        if decoded.lower().startswith("content-length:"):
            try:
                content_length = int(decoded.split(":", 1)[1].strip())
            except ValueError:
                logger.warning("Invalid Content-Length header: %s", decoded)

    if content_length < 0:
        raise McpError("Missing Content-Length header")
    return content_length


def _write_framed_message(stdin: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
    """Serialize and write a JSON-RPC message with Content-Length framing.

    Kept for backward compatibility with code that builds ``dict``
    messages directly.  The :class:`StdioTransport` itself works with
    raw ``bytes``.
    """
    body = json.dumps(msg).encode()
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    stdin.write(header + body)


__all__ = [
    "MAX_STDERR_BYTES",
    "StdioTransport",
]
