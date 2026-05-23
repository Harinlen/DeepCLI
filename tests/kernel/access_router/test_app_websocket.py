from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from kernel.access_router.app import _RuntimeWebSocketClient, _send_json_or_closed

pytestmark = pytest.mark.anyio


async def test_send_json_or_closed_suppresses_close_race() -> None:
    websocket = _ClosedWebSocket()

    sent = await _send_json_or_closed(websocket, {"ok": True})

    assert sent is False
    assert websocket.payloads == [{"ok": True}]


async def test_runtime_websocket_wait_closed_observes_disconnect() -> None:
    connection = _RuntimeWebSocketClient(_DisconnectingWebSocket())

    await connection.wait_closed()


async def test_runtime_websocket_consumes_router_ping_during_delivery() -> None:
    websocket = _QueuedWebSocket(
        [
            {
                "jsonrpc": "2.0",
                "method": "_mustang.router/ping",
                "params": {"connection_id": "conn-1"},
            },
            {"jsonrpc": "2.0", "id": "acp-1", "result": {"ok": True}},
        ]
    )
    connection = _RuntimeWebSocketClient(websocket)
    touches: list[bool] = []
    connection.set_activity_callback(lambda: touches.append(True))

    result = await connection._receive_runtime_result("acp-1", None)

    assert result == {"ok": True}
    assert touches == [True, True]
    assert websocket.sent == []


class _ClosedWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class _DisconnectingWebSocket:
    async def receive_json(self) -> dict[str, Any]:
        raise WebSocketDisconnect(code=1001)


class _QueuedWebSocket:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.sent: list[dict[str, Any]] = []

    async def receive_json(self) -> dict[str, Any]:
        await asyncio.sleep(0)
        if not self._payloads:
            raise WebSocketDisconnect(code=1001)
        return self._payloads.pop(0)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
