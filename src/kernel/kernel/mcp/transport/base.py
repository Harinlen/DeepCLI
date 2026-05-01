"""Transport ABC — lowest layer of the MCP client stack.

Every transport handles exactly one concern: shuttling raw JSON-RPC
byte frames between the kernel and a single MCP server process or
endpoint.  Higher layers (``McpClient``) own protocol semantics
(request/response correlation, handshake, reconnection).

Design mirrors the ``@modelcontextprotocol/sdk`` transport contract
used by Claude Code's ``StdioClientTransport``, ``SSEClientTransport``,
``StreamableHTTPClientTransport``, and ``WebSocketTransport``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Transport(ABC):
    """Byte-frame transport for one MCP server connection.

    Lifecycle::

        transport = SomeTransport(...)
        await transport.connect()     # establish link
        await transport.send(frame)   # write a JSON-RPC frame
        frame = await transport.receive()  # block for next frame
        await transport.close()       # graceful teardown

    ``receive()`` must raise :class:`~kernel.mcp.types.TransportClosed`
    on EOF or unrecoverable connection loss so that ``McpClient`` can
    decide whether to reconnect.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish the underlying connection.

        Raises:
            McpError: If the connection cannot be established.
        """

    @abstractmethod
    async def send(self, message: bytes) -> None:
        """Send a single JSON-RPC frame.

        Args:
            message: Complete JSON-RPC message as UTF-8 bytes.

        Raises:
            TransportClosed: If the connection is no longer writable.
        """

    @abstractmethod
    async def receive(self) -> bytes:
        """Block until the next complete JSON-RPC frame arrives.

        Returns:
            Raw UTF-8 bytes of one JSON-RPC message.

        Raises:
            TransportClosed: On EOF, connection reset, or any
                unrecoverable read failure.
        """

    @abstractmethod
    async def close(self) -> None:
        """Gracefully shut down the transport.

        Must be idempotent — calling ``close()`` on an already-closed
        transport is a no-op.
        """

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport believes the link is still alive."""
