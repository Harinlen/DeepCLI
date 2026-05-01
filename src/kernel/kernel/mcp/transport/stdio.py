"""Stdio transport — spawn a local MCP server as a child process.

Mirrors Claude Code's use of ``@modelcontextprotocol/sdk``
``StdioClientTransport``: the child process speaks JSON-RPC over
stdin/stdout using LSP-style ``Content-Length`` header framing.
Stderr is drained in a background task (capped at 64 MB to prevent
memory growth — same cap as CC).

Environment variables in ``command``, ``args``, and ``env`` values
are expanded (``$VAR`` / ``${VAR}``) before spawning, matching CC's
``expandEnvVarsInString()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from kernel.mcp.transport.base import Transport
from kernel.mcp.types import TransportClosed

logger = logging.getLogger(__name__)

# CC caps stderr at 64 MB to avoid memory blow-up on noisy servers.
_STDERR_CAP_BYTES: int = 64 * 1024 * 1024

# Graceful-close timeouts (seconds).
_WAIT_AFTER_EOF: float = 5.0
_WAIT_AFTER_TERM: float = 2.0


class StdioTransport(Transport):
    """Spawn a subprocess and communicate via stdin/stdout.

    Args:
        command: Executable to run.
        args: Command-line arguments.
        env: Extra environment variables merged with ``os.environ``.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = _expand_env(command)
        self._args = [_expand_env(a) for a in (args or [])]
        self._env = _build_env(env)
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_buf = bytearray()
        self._connected = False

    # ── Transport interface ─────────────────────────────────────────

    async def connect(self) -> None:
        """Spawn the child process."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
        except FileNotFoundError as exc:
            raise TransportClosed(f"command not found: {self._command}") from exc
        except OSError as exc:
            raise TransportClosed(f"failed to spawn {self._command}: {exc}") from exc

        self._connected = True
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name=f"mcp-stderr-{self._command}"
        )
        logger.debug(
            "StdioTransport: spawned pid=%d cmd=%s",
            self._process.pid,
            self._command,
        )

    async def send(self, message: bytes) -> None:
        """Write one Content-Length framed message to stdin."""
        proc = self._process
        if proc is None or proc.stdin is None:
            raise TransportClosed("process not running")

        frame = f"Content-Length: {len(message)}\r\n\r\n".encode() + message
        try:
            proc.stdin.write(frame)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            self._connected = False
            raise TransportClosed(f"stdin write failed: {exc}") from exc

    async def receive(self) -> bytes:
        """Read one Content-Length framed message from stdout."""
        proc = self._process
        if proc is None or proc.stdout is None:
            raise TransportClosed("process not running")

        try:
            content_length = await self._read_content_length(proc.stdout)
            body = await self._read_exact(proc.stdout, content_length)
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            self._connected = False
            raise TransportClosed("stdout EOF or read cancelled")
        except OSError as exc:
            self._connected = False
            raise TransportClosed(f"stdout read failed: {exc}") from exc

        return body

    async def close(self) -> None:
        """Gracefully stop the child process.

        Sequence: close stdin → wait → SIGTERM → wait → SIGKILL.
        Mirrors CC's process cleanup pattern.
        """
        if self._process is None:
            return
        self._connected = False
        proc = self._process
        self._process = None

        # Close stdin to signal EOF to the child.
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()

        # Wait for graceful exit.
        try:
            await asyncio.wait_for(proc.wait(), timeout=_WAIT_AFTER_EOF)
            logger.debug("StdioTransport: process exited gracefully")
            await self._cleanup_stderr()
            return
        except asyncio.TimeoutError:
            pass

        # SIGTERM.
        try:
            proc.terminate()
        except ProcessLookupError:
            await self._cleanup_stderr()
            return

        try:
            await asyncio.wait_for(proc.wait(), timeout=_WAIT_AFTER_TERM)
            logger.debug("StdioTransport: process exited after SIGTERM")
            await self._cleanup_stderr()
            return
        except asyncio.TimeoutError:
            pass

        # SIGKILL (last resort).
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        logger.warning("StdioTransport: process killed (SIGKILL)")
        await self._cleanup_stderr()

    @property
    def is_connected(self) -> bool:
        """True while the subprocess is believed to be alive."""
        return self._connected and self._process is not None

    # ── Stderr tail (diagnostics) ───────────────────────────────────

    @property
    def stderr_tail(self) -> str:
        """Last portion of captured stderr (for error diagnostics)."""
        return self._stderr_buf[-4096:].decode(errors="replace")

    # ── Internal helpers ────────────────────────────────────────────

    async def _drain_stderr(self) -> None:
        """Background task: read stderr and buffer up to cap."""
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = await proc.stderr.read(8192)
                if not chunk:
                    break
                if len(self._stderr_buf) < _STDERR_CAP_BYTES:
                    room = _STDERR_CAP_BYTES - len(self._stderr_buf)
                    self._stderr_buf.extend(chunk[:room])
        except (asyncio.CancelledError, OSError):
            pass

    async def _cleanup_stderr(self) -> None:
        """Cancel the stderr drain task if still running."""
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass

    @staticmethod
    async def _read_content_length(reader: asyncio.StreamReader) -> int:
        """Parse LSP ``Content-Length: N\\r\\n\\r\\n`` header."""
        while True:
            line = await reader.readline()
            if not line:
                raise asyncio.IncompleteReadError(b"", None)  # type: ignore[arg-type]
            text = line.decode(errors="replace").strip()
            if text.lower().startswith("content-length:"):
                length = int(text.split(":", 1)[1].strip())
                # Consume the blank line after the header.
                blank = await reader.readline()
                if not blank:
                    raise asyncio.IncompleteReadError(b"", None)  # type: ignore[arg-type]
                return length
            # Ignore other headers (there shouldn't be any, but be robust).
            if text == "":
                # Empty line without a Content-Length header seen — retry.
                continue

    @staticmethod
    async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
        """Read exactly *n* bytes from *reader*."""
        data = await reader.readexactly(n)
        return data


# ── Environment variable expansion ──────────────────────────────────

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _expand_env(value: str) -> str:
    """Expand ``$VAR`` and ``${VAR}`` references in *value*.

    Mirrors CC's ``expandEnvVarsInString()``.  Undefined variables
    expand to the empty string (with a debug log).
    """

    def _replace(m: re.Match[str]) -> str:
        var = m.group(1) or m.group(2)
        result = os.environ.get(var)
        if result is None:
            logger.debug("env var $%s is not set — expanding to empty", var)
            return ""
        return result

    return _ENV_VAR_RE.sub(_replace, value)


def _build_env(extra: dict[str, str] | None) -> dict[str, str]:
    """Merge *extra* vars into a copy of ``os.environ``.

    Values in *extra* are expanded before merging.
    """
    env = dict(os.environ)
    if extra:
        for key, value in extra.items():
            env[key] = _expand_env(value)
    return env
