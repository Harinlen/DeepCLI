"""Runtime WebBridge manager for browser extension connections."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

import orjson
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from kernel.agents.mustang.tools.web.web_bridge.install_assets import unpacked_extension_path
from kernel.agents.mustang.tools.web.web_bridge.protocol import (
    PROTOCOL_VERSION,
    BrowserInfo,
    WebBridgeFetchRequest,
    WebBridgeFetchResult,
    WebBridgeHello,
    WebBridgeHelloAck,
    WebBridgeStatus,
)

PairPersist = Callable[[str, str], Awaitable[None]]
PairReset = Callable[[], Awaitable[None]]
SecretReader = Callable[[], str | None]


class WebBridgeManager:
    """Owns the local WebBridge WebSocket and connected extension."""

    def __init__(
        self,
        *,
        access_port: int | None = None,
        bridge_port: int | None = None,
        persist_pairing: PairPersist | None = None,
        reset_pairing: PairReset | None = None,
        read_secret: SecretReader | None = None,
    ) -> None:
        self._access_port = access_port or 8200
        self._preferred_bridge_port = bridge_port if bridge_port is not None else self._access_port + 1
        self._persist_pairing = persist_pairing
        self._reset_pairing = reset_pairing
        self._read_secret = read_secret
        self._server: Any = None
        self._ws: Any = None
        self._browser: BrowserInfo | None = None
        self._pairing_token: str | None = None
        self._pairing_expires_at = 0.0
        self._pending: dict[str, asyncio.Future[WebBridgeFetchResult]] = {}
        self._lock = asyncio.Lock()
        self._port = 0

    async def startup(self) -> None:
        """Start the loopback WebBridge server."""

        if self._server is not None:
            return
        try:
            self._server = await websockets.serve(
                self._handle_ws,
                "127.0.0.1",
                self._preferred_bridge_port,
            )
        except OSError:
            self._server = await websockets.serve(self._handle_ws, "127.0.0.1", 0)
        sockets = getattr(self._server, "sockets", None) or []
        if sockets:
            self._port = int(sockets[0].getsockname()[1])

    async def shutdown(self) -> None:
        """Close connections and fail pending fetches."""

        async with self._lock:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("WebBridge shutting down"))
            self._pending.clear()
            if self._ws is not None:
                await self._ws.close()
            self._ws = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def pair_start(self) -> dict[str, Any]:
        """Create a short-lived pairing token."""

        self._pairing_token = f"{secrets.randbelow(1_000_000):06d}"
        self._pairing_expires_at = time.monotonic() + 600
        return self.status(include_pairing_token=True)

    async def pair_reset(self) -> dict[str, Any]:
        """Clear live pairing state and disconnect the extension."""

        self._pairing_token = None
        self._pairing_expires_at = 0.0
        if self._reset_pairing is not None:
            await self._reset_pairing()
        async with self._lock:
            if self._ws is not None:
                await self._ws.close()
            self._ws = None
            self._browser = None
        return self.status()

    def status(self, *, include_pairing_token: bool = False) -> dict[str, Any]:
        """Return public WebBridge status."""

        paired = bool(self._stored_secret())
        connected = self._ws is not None
        if connected:
            status = "available"
            message = "WebBridge extension connected."
        elif paired:
            status = "configured"
            message = "WebBridge extension is paired but offline."
        else:
            status = "setup_needed"
            message = "WebBridge extension is not paired."
        payload = WebBridgeStatus(
            status=status,
            installUrl=self.install_url,
            bridgeWsUrl=self.bridge_ws_url,
            paired=paired,
            connected=connected,
            browser=self._browser,
            message=message,
            pairingToken=self._pairing_token if include_pairing_token else None,
            unpackedPath=str(unpacked_extension_path()),
            zipUrl=self.zip_url,
        )
        return payload.model_dump(by_alias=True)

    async def fetch_tab(self, url: str, *, max_chars: int = 50_000) -> WebBridgeFetchResult:
        """Ask the connected extension to fetch a page in a managed tab."""

        async with self._lock:
            if self._ws is None:
                raise RuntimeError("WebBridge extension is not connected")
            request = WebBridgeFetchRequest(
                id=f"fetch-{secrets.token_hex(8)}",
                url=url,
                maxTextChars=max_chars,
            )
            future: asyncio.Future[WebBridgeFetchResult] = asyncio.get_running_loop().create_future()
            self._pending[request.id] = future
            await self._ws.send(request.model_dump_json(by_alias=True))
        try:
            return await asyncio.wait_for(future, timeout=50)
        finally:
            self._pending.pop(request.id, None)

    @property
    def bridge_ws_url(self) -> str:
        return f"ws://127.0.0.1:{self._port}/web-bridge"

    @property
    def install_url(self) -> str:
        return f"http://127.0.0.1:{self._access_port}/web-bridge/install"

    @property
    def zip_url(self) -> str:
        return f"http://127.0.0.1:{self._access_port}/web-bridge/deepcli-web-bridge.zip"

    async def _handle_ws(self, ws: Any) -> None:
        try:
            raw = await ws.recv()
            hello = WebBridgeHello.model_validate(orjson.loads(raw))
            ack = await self._accept_hello(ws, hello)
            await ws.send(ack.model_dump_json(by_alias=True))
            if not ack.ok:
                await ws.close()
                return
            async for message in ws:
                await self._handle_message(message)
        except ConnectionClosedOK:
            return
        except ConnectionClosed:
            return
        finally:
            async with self._lock:
                if self._ws is ws:
                    self._ws = None
                    self._browser = None

    async def _accept_hello(self, ws: Any, hello: WebBridgeHello) -> WebBridgeHelloAck:
        if hello.protocol_version != PROTOCOL_VERSION:
            return WebBridgeHelloAck(id=hello.id, ok=False, message="unsupported protocol")
        stored = self._stored_secret()
        if hello.secret and stored and secrets.compare_digest(hello.secret, stored):
            async with self._lock:
                if self._ws is not None and self._ws is not ws:
                    await self._ws.close()
                self._ws = ws
                self._browser = hello.browser
            return WebBridgeHelloAck(id=hello.id, ok=True)
        if not self._pairing_token or time.monotonic() > self._pairing_expires_at:
            return WebBridgeHelloAck(id=hello.id, ok=False, message="pairing token expired")
        if not hello.pairing_token or not secrets.compare_digest(
            hello.pairing_token,
            self._pairing_token,
        ):
            return WebBridgeHelloAck(id=hello.id, ok=False, message="invalid pairing token")
        new_secret = secrets.token_urlsafe(32)
        if self._persist_pairing is not None:
            await self._persist_pairing(hello.extension_id, new_secret)
        self._pairing_token = None
        self._pairing_expires_at = 0.0
        async with self._lock:
            if self._ws is not None and self._ws is not ws:
                await self._ws.close()
            self._ws = ws
            self._browser = hello.browser
        return WebBridgeHelloAck(id=hello.id, ok=True, secret=new_secret)

    async def _handle_message(self, message: str | bytes) -> None:
        payload = orjson.loads(message)
        if payload.get("type") == "heartbeat":
            return
        if payload.get("type") != "fetch_result":
            return
        result = WebBridgeFetchResult.model_validate(payload)
        future = self._pending.get(result.id)
        if future is not None and not future.done():
            future.set_result(result)

    def _stored_secret(self) -> str | None:
        if self._read_secret is None:
            return None
        return self._read_secret()


__all__ = ["WebBridgeManager"]
