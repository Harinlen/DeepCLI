"""Discord Gateway WebSocket connection lifecycle.

Manages the outbound long-lived WebSocket connection to Discord's
Gateway API (wss://gateway.discord.gg).  Responsibilities:

- IDENTIFY handshake (sends bot credentials, receives session_id).
- Heartbeat loop (keeps the connection alive per Discord's protocol).
- RECONNECT / INVALID_SESSION opcode handling.
- Dispatching ``DISPATCH`` events (op 0) to a caller-supplied callback.

This is an internal implementation detail of ``DiscordAdapter``; nothing
outside ``kernel.gateways.discord`` should import from this module.

References
----------
Discord Gateway documentation:
  https://discord.com/developers/docs/topics/gateway
"""

from __future__ import annotations

import asyncio
import orjson
import logging
import random
from collections.abc import Callable, Coroutine
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

# Discord Gateway URL (v10).
_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# Gateway opcodes we care about.
_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11

# Type for the event callback supplied by DiscordAdapter.
EventCallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


class DiscordGateway:
    """Manages one Discord Gateway WebSocket connection.

    The caller supplies an ``on_event`` coroutine that is called for
    every ``DISPATCH`` event (op 0) the gateway receives.

    Usage::

        gw = DiscordGateway(token=bot_token, on_event=handler)
        await gw.connect()   # blocks until disconnect or error
        await gw.close()

    Args:
        token: Discord bot token (``"Bot <token>"``).
        on_event: Async callback invoked as ``on_event(event_name, data)``
            for each inbound DISPATCH event.
    """

    def __init__(self, token: str, on_event: EventCallback) -> None:
        self._token = token
        self._on_event = on_event

        self._ws: Any = None  # websockets.WebSocketClientProtocol
        self._heartbeat_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._sequence: int | None = None
        self._closed = False

    async def connect(self) -> None:
        """Open the Gateway connection and run the receive loop.

        Reconnects automatically on transient disconnects.  Returns only
        when ``close()`` is called or a non-recoverable error occurs.
        """
        backoff = 1.0
        while not self._closed:
            try:
                await self._run_once()
                backoff = 1.0  # successful session resets back-off
            except ConnectionClosed as exc:
                if self._closed:
                    return
                logger.warning(
                    "Discord Gateway connection closed (code=%s) — reconnecting in %.0fs",
                    exc.code,
                    backoff,
                )
            except Exception:
                if self._closed:
                    return
                logger.exception("Discord Gateway error — reconnecting in %.0fs", backoff)
            if not self._closed:
                await asyncio.sleep(backoff)
                # Exponential back-off, capped at 5 minutes.
                backoff = min(backoff * 2 + random.uniform(0, 1), 300.0)  # nosec B311

    async def close(self) -> None:
        """Signal the connection to close and cancel the heartbeat task."""
        self._closed = True
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # nosec B110 — best-effort WS close
                pass

    # ------------------------------------------------------------------
    # Internal: single connection lifetime
    # ------------------------------------------------------------------

    async def _run_once(self) -> None:
        """Open one WebSocket connection and run until it closes."""
        async with websockets.connect(_GATEWAY_URL) as ws:  # type: ignore[attr-defined]
            self._ws = ws
            try:
                async for raw in ws:
                    payload = orjson.loads(raw)
                    op: int = payload["op"]
                    data: Any = payload.get("d")
                    seq: int | None = payload.get("s")
                    event: str | None = payload.get("t")

                    if seq is not None:
                        self._sequence = seq

                    if op == _OP_HELLO:
                        interval_ms: int = data["heartbeat_interval"]
                        self._heartbeat_task = asyncio.create_task(
                            self._heartbeat_loop(ws, interval_ms)
                        )
                        await self._identify(ws)

                    elif op == _OP_HEARTBEAT:
                        # Server explicitly requests an immediate heartbeat.
                        await ws.send(orjson.dumps({"op": _OP_HEARTBEAT, "d": self._sequence}).decode())

                    elif op == _OP_HEARTBEAT_ACK:
                        pass  # latency tracking could go here

                    elif op == _OP_DISPATCH and event is not None:
                        try:
                            await self._on_event(event, data or {})
                        except Exception:
                            logger.exception("Discord Gateway on_event raised for event=%s", event)

                    elif op == _OP_RECONNECT:
                        logger.info("Discord Gateway: server requested reconnect")
                        break  # outer loop reconnects

                    elif op == _OP_INVALID_SESSION:
                        logger.warning("Discord Gateway: invalid session — re-identifying")
                        # Brief pause Discord recommends before re-IDENTIFY.
                        await asyncio.sleep(random.uniform(1, 5))  # nosec B311
                        await self._identify(ws)
            finally:
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                    try:
                        await self._heartbeat_task
                    except asyncio.CancelledError:
                        pass

    async def _identify(self, ws: Any) -> None:
        """Send the IDENTIFY payload with bot credentials."""
        payload = {
            "op": _OP_IDENTIFY,
            "d": {
                "token": self._token,
                "intents": 37377,  # GUILDS(1) | GUILD_MESSAGES(512) | DIRECT_MESSAGES(4096) | MESSAGE_CONTENT(32768)
                "properties": {
                    "os": "linux",
                    "browser": "mustang",
                    "device": "mustang",
                },
            },
        }
        await ws.send(orjson.dumps(payload).decode())

    async def _heartbeat_loop(self, ws: Any, interval_ms: int) -> None:
        """Send heartbeat frames at the interval Discord specified in HELLO.

        Args:
            ws: Open WebSocket connection.
            interval_ms: Heartbeat interval in milliseconds from HELLO payload.
        """
        # Jitter the first heartbeat per Discord's recommendation.
        await asyncio.sleep(random.uniform(0, interval_ms / 1000))  # nosec B311
        while True:
            try:
                await ws.send(orjson.dumps({"op": _OP_HEARTBEAT, "d": self._sequence}).decode())
                await asyncio.sleep(interval_ms / 1000)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Discord Gateway heartbeat error")
                return
