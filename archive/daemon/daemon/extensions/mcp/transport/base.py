"""Transport ABC for MCP client connections.

Defines the interface that all MCP transport implementations must
satisfy.  The :class:`McpClient` depends only on this interface,
making it transport-agnostic — stdio, in-process queues, HTTP/SSE,
and WebSocket transports all plug in through the same contract.

The key design choice is a **blocking receive** (``await receive()``)
rather than a callback-based approach.  This gives the client a
simple ``while True: body = await transport.receive()`` loop that
is easy to cancel via structured concurrency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TransportClosed(Exception):
    """Raised by :meth:`Transport.receive` when the connection is lost.

    The MCP client catches this to trigger its reconnect logic.
    Subclasses should raise it on EOF, connection reset, or any
    unrecoverable read failure.
    """


class Transport(ABC):
    """MCP transport layer — manages connection lifecycle and framing.

    Implementers handle the details of a specific wire protocol
    (subprocess stdio, asyncio queues, HTTP/SSE, WebSocket) while
    the :class:`McpClient` handles JSON-RPC correlation, MCP
    handshake, and reconnection policy.

    Lifecycle contract::

        transport = SomeTransport(...)
        await transport.connect()          # establish connection
        await transport.send(payload)      # send JSON-RPC message
        body = await transport.receive()   # block until next message
        await transport.close()            # graceful shutdown

    All methods are async.  ``close()`` must be idempotent — calling
    it on an already-closed transport is a no-op.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish the underlying connection.

        For stdio: spawn subprocess.  For HTTP/SSE: open stream.
        For WebSocket: connect.  For in-process: start server task.

        Raises:
            McpError: If the connection cannot be established.
        """

    @abstractmethod
    async def send(self, message: bytes) -> None:
        """Send a framed JSON-RPC message.

        Args:
            message: Serialized JSON-RPC payload (raw bytes).

        Raises:
            TransportClosed: If the connection is no longer writable.
        """

    @abstractmethod
    async def receive(self) -> bytes:
        """Block until the next complete JSON-RPC message arrives.

        Returns:
            Raw bytes of a single JSON-RPC message body.

        Raises:
            TransportClosed: On EOF, connection lost, or cancellation.
        """

    @abstractmethod
    async def close(self) -> None:
        """Gracefully shut down the connection.  Idempotent."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport has an active, usable connection."""


__all__ = ["Transport", "TransportClosed"]
