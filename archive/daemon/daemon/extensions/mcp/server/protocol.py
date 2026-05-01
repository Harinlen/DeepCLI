"""ABC for in-process MCP servers.

Defines the minimal interface that a Python class must implement to
serve as an MCP server running inside the daemon process.  The
server communicates via two ``asyncio.Queue`` channels — one for
incoming requests (client → server) and one for outgoing responses
(server → client).

Messages on the queues are raw ``bytes`` containing JSON-RPC 2.0
payloads.  This keeps the protocol consistent with stdio and remote
transports — :class:`McpClient` does not need any special handling
for in-process servers.

Example::

    class EchoServer(McpServerProtocol):
        async def run(self, inbox, outbox) -> None:
            while True:
                raw = await inbox.get()
                req = json.loads(raw)
                resp = self._handle(req)
                await outbox.put(json.dumps(resp).encode())

        def capabilities(self) -> dict[str, Any]:
            return {"tools": {}}
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any


class McpServerProtocol(ABC):
    """In-process MCP server interface.

    Implementers handle the full JSON-RPC lifecycle: read requests
    from *inbox*, process them, and write responses to *outbox*.
    The ``run`` coroutine should loop until cancelled or until
    *inbox* signals shutdown (implementation-defined).
    """

    @abstractmethod
    async def run(
        self,
        inbox: asyncio.Queue[bytes],
        outbox: asyncio.Queue[bytes],
    ) -> None:
        """Main server loop — read requests, write responses.

        Args:
            inbox: Client-to-server message queue (JSON-RPC bytes).
            outbox: Server-to-client message queue (JSON-RPC bytes).

        The method should run until the task is cancelled.  On
        cancellation it should clean up and return promptly.
        """

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return MCP server capabilities for the initialize handshake.

        The returned dict is used by the ``initialize`` response to
        inform the client what protocols this server supports (e.g.
        ``{"tools": {}, "resources": {}}``).
        """


__all__ = ["McpServerProtocol"]
