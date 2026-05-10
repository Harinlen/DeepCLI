from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kernel.agents.mustang.mcp.config import HTTPServerConfig, SSEServerConfig, StdioServerConfig, WebSocketServerConfig
from kernel.agents.mustang.mcp.health import _sweep, health_loop
from kernel.agents.mustang.mcp.transport import create_transport
from kernel.agents.mustang.mcp.transport.http import HTTPTransport
from kernel.agents.mustang.mcp.transport.sse import SSETransport
from kernel.agents.mustang.mcp.transport.stdio import StdioTransport
from kernel.agents.mustang.mcp.transport.ws import WebSocketTransport
from kernel.agents.mustang.mcp.types import ConnectedServer, FailedServer, TransportClosed


class _Signal:
    def __init__(self) -> None:
        self.count = 0

    async def emit(self) -> None:
        self.count += 1


class _Manager:
    def __init__(self, connections: dict[str, object], reconnects: dict[str, object]) -> None:
        self._connections = connections
        self._reconnects = reconnects
        self.on_tools_changed = _Signal()

    def get_connections(self) -> dict[str, object]:
        return self._connections

    async def reconnect(self, name: str) -> object:
        return self._reconnects[name]


def _failed(name: str = "bad") -> FailedServer:
    return FailedServer(name=name, error="nope")


def _connected(name: str = "ok") -> ConnectedServer:
    return ConnectedServer(name=name, client=SimpleNamespace())


async def test_sweep_reconnects_failed_servers_and_reports_change() -> None:
    manager = _Manager(
        {"bad": _failed(), "ok": _connected()},
        {"bad": _connected("bad")},
    )

    assert await _sweep(manager) is True


async def test_sweep_returns_false_when_reconnect_still_failed() -> None:
    manager = _Manager({"bad": _failed()}, {"bad": _failed()})

    assert await _sweep(manager) is False


async def test_health_loop_emits_when_sweep_changes_and_exits_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager({"bad": _failed()}, {"bad": _connected("bad")})
    sleeps = 0

    async def fast_sleep(delay: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr("kernel.agents.mustang.mcp.health.asyncio.sleep", fast_sleep)

    await health_loop(manager, interval=0)

    assert manager.on_tools_changed.count == 1


def test_create_transport_instantiates_all_supported_transports() -> None:
    stdio = create_transport("s", StdioServerConfig(command="python"))
    sse = create_transport(
        "sse",
        SSEServerConfig(type="sse", url="http://example.test/sse", headers={"A": "old"}),
        auth_headers={"A": "new", "B": "two"},
    )
    http = create_transport(
        "http",
        HTTPServerConfig(type="http", url="http://example.test/mcp"),
        auth_headers={"Authorization": "Bearer token"},
    )
    ws = create_transport("ws", WebSocketServerConfig(type="ws", url="ws://example.test"))

    assert isinstance(stdio, StdioTransport)
    assert isinstance(sse, SSETransport)
    assert isinstance(http, HTTPTransport)
    assert isinstance(ws, WebSocketTransport)
    assert sse._headers == {"A": "new", "B": "two"}
    assert http._headers == {"Authorization": "Bearer token"}


def test_create_transport_rejects_unknown_config() -> None:
    with pytest.raises(ValueError, match="unknown MCP server config type"):
        create_transport("bad", object())  # type: ignore[arg-type]


async def test_websocket_transport_send_receive_close_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWs:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed = False
            self.recv = AsyncMock(return_value="reply")

        async def send(self, data: str) -> None:
            self.sent.append(data)

        async def close(self) -> None:
            self.closed = True

    ws = FakeWs()

    async def connect(url: str, additional_headers: dict[str, str]):
        assert url == "ws://example.test"
        assert additional_headers == {"X": "1"}
        return ws

    monkeypatch.setitem(__import__("sys").modules, "websockets", SimpleNamespace(connect=connect))
    transport = WebSocketTransport("ws://example.test", {"X": "1"}, "test")

    await transport.connect()
    await transport.send(b'{"jsonrpc":"2.0"}')
    received = await transport.receive()
    await transport.close()

    assert transport.is_connected is False
    assert ws.sent == ['{"jsonrpc":"2.0"}']
    assert received == b"reply"
    assert ws.closed is True


async def test_websocket_transport_requires_connected_state_and_wraps_failures() -> None:
    transport = WebSocketTransport("ws://example.test")

    with pytest.raises(TransportClosed, match="not connected"):
        await transport.send(b"{}")
    with pytest.raises(TransportClosed, match="not connected"):
        await transport.receive()

    transport._connected = True
    transport._ws = SimpleNamespace(
        send=AsyncMock(side_effect=RuntimeError("send fail")),
        recv=AsyncMock(side_effect=RuntimeError("recv fail")),
        close=AsyncMock(side_effect=RuntimeError("close fail")),
    )

    with pytest.raises(TransportClosed, match="send failed"):
        await transport.send(b"{}")

    transport._connected = True
    with pytest.raises(TransportClosed, match="recv failed"):
        await transport.receive()

    await transport.close()
    assert transport._ws is None
