"""In-process transport — MCP over asyncio queues.

Runs a :class:`McpServerProtocol` implementation in a background
``asyncio.Task`` within the same process.  Communication happens
via a pair of ``asyncio.Queue`` objects — zero serialization
overhead, no subprocess management.

Typical use cases:
- Built-in servers (e.g. filesystem, database)
- User-defined Python MCP servers loaded via config
- Testing (echo / calculator servers)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from daemon.extensions.mcp.transport.base import Transport, TransportClosed

logger = logging.getLogger(__name__)


class InProcessTransport(Transport):
    """Transport over ``asyncio.Queue`` pair for same-process servers.

    The server is started as an ``asyncio.Task`` on ``connect()``
    and cancelled on ``close()``.  Messages flow through two
    unbounded queues — one for each direction.

    Args:
        server_factory: Callable that returns a new
            :class:`McpServerProtocol` instance.  Called once per
            ``connect()`` invocation.
        name: Logical name for logging.
    """

    def __init__(
        self,
        server_factory: callable,  # type: ignore[type-arg]
        name: str = "inprocess",
    ) -> None:
        self._server_factory = server_factory
        self._name = name
        self._to_server: asyncio.Queue[bytes] = asyncio.Queue()
        self._to_client: asyncio.Queue[bytes] = asyncio.Queue()
        self._server_task: asyncio.Task[None] | None = None
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Transport interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start the in-process MCP server task.

        Creates fresh queues and spawns the server's ``run()``
        coroutine as a background task.
        """
        # Fresh queues for each connection (supports reconnect)
        self._to_server = asyncio.Queue()
        self._to_client = asyncio.Queue()

        server = self._server_factory()
        self._server_task = asyncio.create_task(
            server.run(self._to_server, self._to_client),
            name=f"mcp-inprocess-{self._name}",
        )
        self._connected = True
        logger.debug("In-process transport connected for '%s'", self._name)

    async def send(self, message: bytes) -> None:
        """Enqueue a message for the server.

        Args:
            message: Serialized JSON-RPC payload.

        Raises:
            TransportClosed: If the transport is not connected or
                the server task has exited.
        """
        if not self._connected:
            raise TransportClosed("In-process transport not connected")
        if self._server_task and self._server_task.done():
            self._connected = False
            raise TransportClosed("In-process server task exited")
        await self._to_server.put(message)

    async def receive(self) -> bytes:
        """Wait for the next message from the server.

        Returns:
            Raw JSON-RPC response bytes.

        Raises:
            TransportClosed: If the transport is closed or the server
                exits unexpectedly.
        """
        if not self._connected:
            raise TransportClosed("In-process transport not connected")

        # Race between getting a message and server task dying
        get_task = asyncio.create_task(self._to_client.get())
        try:
            if self._server_task and not self._server_task.done():
                done, _ = await asyncio.wait(
                    [get_task, self._server_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if get_task in done:
                    return get_task.result()
                # Server died — check if there's still data in queue
                if not self._to_client.empty():
                    get_task.cancel()
                    return self._to_client.get_nowait()
                get_task.cancel()
                self._connected = False
                raise TransportClosed("In-process server task exited")
            else:
                # No server task — just try the queue
                return await get_task
        except asyncio.CancelledError:
            get_task.cancel()
            raise TransportClosed("Receive cancelled") from None

    async def close(self) -> None:
        """Cancel the server task and mark as disconnected.  Idempotent."""
        self._connected = False
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._server_task
            self._server_task = None
        logger.debug("In-process transport closed for '%s'", self._name)

    @property
    def is_connected(self) -> bool:
        """True if connected and server task is still running."""
        if not self._connected:
            return False
        if self._server_task and self._server_task.done():
            self._connected = False
            return False
        return True


__all__ = ["InProcessTransport"]
