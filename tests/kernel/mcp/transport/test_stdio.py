"""Tests for kernel.mcp.transport.stdio — StdioTransport."""

from __future__ import annotations

import asyncio
import sys

import pytest

from kernel.mcp.transport.stdio import StdioTransport, _expand_env
from kernel.mcp.types import TransportClosed


class TestExpandEnv:
    """Environment variable expansion."""

    def test_dollar_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "bar")
        assert _expand_env("$FOO") == "bar"

    def test_braced_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "bar")
        assert _expand_env("${FOO}") == "bar"

    def test_undefined_expands_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("UNDEFINED_VAR_XYZ", raising=False)
        assert _expand_env("$UNDEFINED_VAR_XYZ") == ""

    def test_mixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _expand_env("$A-${B}-end") == "1-2-end"

    def test_no_vars(self) -> None:
        assert _expand_env("plain text") == "plain text"


class TestStdioTransport:
    """StdioTransport lifecycle tests."""

    @pytest.mark.anyio
    async def test_connect_nonexistent_command(self) -> None:
        """connect() raises TransportClosed for a missing binary."""
        transport = StdioTransport(command="/nonexistent/binary_xyz")
        with pytest.raises(TransportClosed, match="command not found"):
            await transport.connect()

    @pytest.mark.anyio
    async def test_connect_and_close(self) -> None:
        """Can connect to a real process and close gracefully."""
        # Use 'cat' as a simple process that reads stdin until EOF.
        transport = StdioTransport(command="cat")
        await transport.connect()
        assert transport.is_connected

        await transport.close()
        assert not transport.is_connected

    @pytest.mark.anyio
    async def test_send_and_receive(self) -> None:
        """Round-trip a Content-Length framed message through 'cat'."""
        # 'cat' echoes stdin to stdout — exactly what we need for
        # Content-Length framing round-trip.
        transport = StdioTransport(command="cat")
        await transport.connect()

        msg = b'{"jsonrpc":"2.0","id":1}'
        await transport.send(msg)

        received = await transport.receive()
        assert received == msg

        await transport.close()

    @pytest.mark.anyio
    async def test_receive_after_close(self) -> None:
        """receive() raises TransportClosed after process exits."""
        transport = StdioTransport(command="true")  # exits immediately
        await transport.connect()

        # Give the process a moment to exit.
        await asyncio.sleep(0.1)

        with pytest.raises(TransportClosed):
            await transport.receive()

    @pytest.mark.anyio
    async def test_close_idempotent(self) -> None:
        """Multiple close() calls are safe."""
        transport = StdioTransport(command="cat")
        await transport.connect()
        await transport.close()
        await transport.close()  # no error

    @pytest.mark.anyio
    async def test_stderr_captured(self) -> None:
        """stderr output is captured in stderr_tail."""
        # Write to stderr and exit.
        transport = StdioTransport(
            command=sys.executable,
            args=["-c", "import sys; sys.stderr.write('err msg'); sys.exit(0)"],
        )
        await transport.connect()
        await asyncio.sleep(0.2)  # let process write + exit
        await transport.close()

        assert "err msg" in transport.stderr_tail
