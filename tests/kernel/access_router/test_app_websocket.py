from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from kernel.access_router.schemas import RuntimeAcpRequest
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


async def test_runtime_websocket_close_cancels_reader_task() -> None:
    websocket = _BlockingWebSocket()
    connection = _RuntimeWebSocketClient(websocket)
    wait_task = asyncio.create_task(connection.wait_closed())
    await asyncio.sleep(0)

    await connection.close()

    await asyncio.wait_for(wait_task, timeout=1)
    assert websocket.closed is True


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


async def test_runtime_websocket_allows_control_request_while_prompt_is_pending() -> None:
    websocket = _ConcurrentWebSocket()
    connection = _RuntimeWebSocketClient(websocket)

    prompt_task = asyncio.create_task(
        connection.deliver_acp(RuntimeAcpRequest(agent_id="primary", method="session/prompt", params={}))
    )
    mode_task = asyncio.create_task(
        connection.deliver_acp(
            RuntimeAcpRequest(
                agent_id="primary",
                method="session/set_mode",
                params={"sessionId": "s-1", "modeId": "bypass"},
            )
        )
    )

    assert await asyncio.wait_for(mode_task, timeout=1) == {"ok": True, "mode": "bypass"}
    websocket.release_prompt()
    assert await asyncio.wait_for(prompt_task, timeout=1) == {"ok": True, "stopReason": "end_turn"}
    assert [payload["method"] for payload in websocket.sent] == [
        "_mustang.runtime/request",
        "_mustang.runtime/request",
    ]


class _ClosedWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class _DisconnectingWebSocket:
    async def receive_json(self) -> dict[str, Any]:
        raise WebSocketDisconnect(code=1001)


class _BlockingWebSocket:
    def __init__(self) -> None:
        self.closed = False

    async def receive_json(self) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {}

    async def close(self) -> None:
        self.closed = True


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


class _ConcurrentWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self._prompt_release = asyncio.Event()
        self._returned_mode = False
        self._returned_prompt = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def receive_json(self) -> dict[str, Any]:
        while len(self.sent) < 2:
            await asyncio.sleep(0)
        if not self._returned_mode:
            self._returned_mode = True
            mode_id = self.sent[1]["id"]
            return {"jsonrpc": "2.0", "id": mode_id, "result": {"ok": True, "mode": "bypass"}}
        await self._prompt_release.wait()
        if not self._returned_prompt:
            self._returned_prompt = True
            prompt_id = self.sent[0]["id"]
            return {
                "jsonrpc": "2.0",
                "id": prompt_id,
                "result": {"ok": True, "stopReason": "end_turn"},
            }
        raise WebSocketDisconnect(code=1001)

    def release_prompt(self) -> None:
        self._prompt_release.set()
