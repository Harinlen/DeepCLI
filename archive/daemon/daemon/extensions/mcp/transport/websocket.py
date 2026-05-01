"""WebSocket transport — MCP over persistent WebSocket connections.

Full-duplex transport for MCP servers that expose a WebSocket
endpoint.  Messages are sent and received as text frames containing
JSON-RPC payloads.
"""

from __future__ import annotations

import asyncio
import logging

import websockets
import websockets.asyncio.client
from websockets.exceptions import ConnectionClosed

from daemon.extensions.mcp.transport.base import Transport, TransportClosed

logger = logging.getLogger(__name__)

# Connection timeout (seconds).
_CONNECT_TIMEOUT = 15.0


class WebSocketTransport(Transport):
    """MCP transport over WebSocket.

    Connects to a remote MCP server via a persistent WebSocket
    connection.  Both send and receive are direct frame operations
    — no additional framing layer needed.

    Args:
        url: WebSocket endpoint URL (``ws://`` or ``wss://``).
        headers: Additional headers for the upgrade request
            (e.g. Authorization).
        name: Logical name for logging.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        name: str = "ws",
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._name = name
        self._ws: websockets.asyncio.client.ClientConnection | None = None

    # ------------------------------------------------------------------
    # Transport interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket connection.

        Raises:
            TransportClosed: If the connection cannot be established.
        """
        try:
            self._ws = await asyncio.wait_for(
                websockets.asyncio.client.connect(
                    self._url,
                    additional_headers=self._headers,
                ),
                timeout=_CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise TransportClosed(f"WebSocket connect failed for '{self._name}': {exc}") from exc

        logger.debug(
            "WebSocket transport connected for '%s' — %s",
            self._name,
            self._url,
        )

    async def send(self, message: bytes) -> None:
        """Send a JSON-RPC message as a WebSocket text frame.

        Args:
            message: Serialized JSON-RPC payload.

        Raises:
            TransportClosed: If the connection is not open.
        """
        if self._ws is None:
            raise TransportClosed("WebSocket transport not connected")

        try:
            await self._ws.send(message.decode())
        except ConnectionClosed as exc:
            raise TransportClosed(f"WebSocket closed for '{self._name}': {exc}") from exc

    async def receive(self) -> bytes:
        """Wait for the next WebSocket message.

        Returns:
            Raw JSON-RPC message bytes.

        Raises:
            TransportClosed: On connection close or cancellation.
        """
        if self._ws is None:
            raise TransportClosed("WebSocket transport not connected")

        try:
            data = await self._ws.recv()
            if isinstance(data, bytes):
                return data
            return data.encode()
        except ConnectionClosed as exc:
            raise TransportClosed(f"WebSocket closed for '{self._name}': {exc}") from exc
        except asyncio.CancelledError:
            raise TransportClosed("WebSocket receive cancelled") from None

    async def close(self) -> None:
        """Close the WebSocket connection.  Idempotent."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass  # Best-effort close
            self._ws = None
        logger.debug("WebSocket transport closed for '%s'", self._name)

    @property
    def is_connected(self) -> bool:
        """True if the WebSocket is open."""
        return self._ws is not None and self._ws.state.name == "OPEN"


__all__ = ["WebSocketTransport"]
