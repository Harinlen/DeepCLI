"""WebSocket transport — full-duplex connection to a remote MCP server.

Mirrors Claude Code's ``WebSocketTransport``: each WebSocket text
frame carries exactly one JSON-RPC message — no Content-Length
framing needed.  Uses the ``websockets`` library for the connection.

Timeout policy:
- Connection: 15 s (matches CC).
- Read: no timeout (messages arrive when the server is ready).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kernel.mcp.transport.base import Transport
from kernel.mcp.types import TransportClosed

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT: float = 15.0


class WebSocketTransport(Transport):
    """Full-duplex WebSocket transport for one MCP server.

    Args:
        url: WebSocket endpoint (``ws://`` or ``wss://``).
        headers: Extra HTTP headers sent during the upgrade handshake.
        server_name: For log messages.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        server_name: str = "ws",
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._server_name = server_name
        self._ws: Any = None  # websockets client connection
        self._connected = False

    # ── Transport interface ─────────────────────────────────────────

    async def connect(self) -> None:
        """Open the WebSocket connection."""
        try:
            import websockets
        except ImportError as exc:
            raise TransportClosed(
                "websockets package is required for WebSocket transport (pip install websockets)"
            ) from exc

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self._url,
                    additional_headers=self._headers,
                ),
                timeout=_CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise TransportClosed(
                f"WebSocket connection timed out after {_CONNECT_TIMEOUT}s"
            ) from exc
        except Exception as exc:
            raise TransportClosed(f"WebSocket connection failed: {exc}") from exc

        self._connected = True
        logger.debug(
            "WebSocketTransport[%s]: connected to %s",
            self._server_name,
            self._url,
        )

    async def send(self, message: bytes) -> None:
        """Send one JSON-RPC message as a text frame."""
        if not self._connected or self._ws is None:
            raise TransportClosed("WebSocket not connected")

        try:
            await self._ws.send(message.decode())
        except Exception as exc:
            self._connected = False
            raise TransportClosed(f"WebSocket send failed: {exc}") from exc

    async def receive(self) -> bytes:
        """Block until the next text frame arrives."""
        if not self._connected or self._ws is None:
            raise TransportClosed("WebSocket not connected")

        try:
            data = await self._ws.recv()
        except asyncio.CancelledError:
            raise TransportClosed("receive cancelled")
        except Exception as exc:
            self._connected = False
            raise TransportClosed(f"WebSocket recv failed: {exc}") from exc

        if isinstance(data, str):
            return data.encode()
        return data  # type: ignore[return-value]

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass  # best-effort
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._connected
