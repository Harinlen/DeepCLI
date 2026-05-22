from __future__ import annotations

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


class _ClosedWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class _DisconnectingWebSocket:
    async def receive_json(self) -> dict[str, Any]:
        raise WebSocketDisconnect(code=1001)
