"""Minimal durable Agent Runtime websocket backend."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection

from kernel.agents import HubFrame, HubFrameType

RuntimeHandler = Callable[..., Awaitable[HubFrame]]


class RuntimeClientPeer:
    """Bidirectional helper for runtime -> Hub -> Access client requests."""

    def __init__(self, ws: ServerConnection) -> None:
        self._ws = ws
        self._send_lock = asyncio.Lock()
        self._request_counter = 0

    async def request_client(
        self,
        *,
        method: str,
        params: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Ask the connected Hub to proxy one request to the user client."""

        self._request_counter += 1
        frame_id = f"runtime-client-request-{self._request_counter}"
        frame = HubFrame(
            frame_id=frame_id,
            frame_type=HubFrameType.REQUEST,
            contract="client.request",
            payload={"method": method, "params": params},
        )
        async with self._send_lock:
            await self._ws.send(frame.to_json_bytes())
            if timeout is None:
                raw: Any = await self._ws.recv()
            else:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        response = HubFrame.from_json_bytes(raw)
        if response.payload.get("ok") is not True:
            message = response.payload.get("message") or response.payload.get("error")
            raise RuntimeError(str(message or "client request failed"))
        result = response.payload.get("result")
        return result if isinstance(result, dict) else {}


async def _default_handler(frame: HubFrame) -> HubFrame:
    return HubFrame(
        frame_id=f"{frame.frame_id}:response",
        frame_type=HubFrameType.RESPONSE,
        contract=frame.contract,
        correlation_id=frame.frame_id,
        payload={"ok": True},
    )


class MinimalAgentRuntimeServer:
    """Small websockets-based server used for Batch C runtime contract probes."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        handler: RuntimeHandler = _default_handler,
    ) -> None:
        self.host = host
        self.port = port
        self._handler = handler
        self._handler_accepts_peer = len(inspect.signature(handler).parameters) >= 2
        self._server: Server | None = None

    @property
    def endpoint(self) -> str:
        """Return the ws:// endpoint after startup."""

        if self._server is None or not self._server.sockets:
            raise RuntimeError("runtime server is not started")
        port = self._server.sockets[0].getsockname()[1]
        return f"ws://{self.host}:{port}"

    async def start(self) -> None:
        """Start the runtime websocket server."""

        self._server = await websockets.serve(self._handle, self.host, self.port)

    async def stop(self) -> None:
        """Stop the runtime websocket server."""

        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, ws: ServerConnection) -> None:
        peer = RuntimeClientPeer(ws)
        async for raw in ws:
            frame = HubFrame.from_json_bytes(raw)
            if self._handler_accepts_peer:
                response = await self._handler(frame, peer)
            else:
                response = await self._handler(frame)
            await ws.send(response.to_json_bytes())


async def request_runtime(
    endpoint: str,
    frame: HubFrame,
    *,
    timeout: float | None = 5,
    client_request_handler: Callable[[HubFrame], Awaitable[HubFrame]] | None = None,
) -> HubFrame:
    """Send one frame to an Agent Runtime websocket endpoint."""

    async with websockets.connect(endpoint) as ws:
        await ws.send(frame.to_json_bytes())
        while True:
            if timeout is None:
                raw: Any = await ws.recv()
            else:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            response = HubFrame.from_json_bytes(raw)
            if response.frame_type is HubFrameType.REQUEST and response.contract == "client.request":
                if client_request_handler is None:
                    proxied = HubFrame(
                        frame_id=f"{response.frame_id}:error",
                        frame_type=HubFrameType.RESPONSE,
                        contract=response.contract,
                        correlation_id=response.frame_id,
                        payload={"ok": False, "error": "client_request_unavailable"},
                    )
                else:
                    proxied = await client_request_handler(response)
                await ws.send(proxied.to_json_bytes())
                continue
            return response
