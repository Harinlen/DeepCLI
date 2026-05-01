"""MCP client — JSON-RPC 2.0 over pluggable transports.

Protocol-level client for the Model Context Protocol.  Handles the
MCP handshake (``initialize`` / ``initialized``), JSON-RPC request
correlation, and automatic reconnection with exponential back-off.

The actual wire protocol is delegated to a :class:`Transport`
implementation (stdio, in-process, HTTP/SSE, WebSocket).  This
class never touches subprocess handles, sockets, or queues directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from daemon.errors import McpError
from daemon.extensions.mcp.jsonrpc import dispatch_response, reject_all_pending
from daemon.extensions.mcp.transport.base import Transport, TransportClosed

logger = logging.getLogger(__name__)

# Protocol defaults
_MCP_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_NAME = "mustang"
_CLIENT_VERSION = "0.1.0"

# Reconnection
_MAX_RECONNECT_ATTEMPTS = 5
_MAX_RECONNECT_DELAY = 60  # seconds

# Request timeout
_DEFAULT_REQUEST_TIMEOUT = 30  # seconds


class McpClient:
    """Transport-agnostic MCP client for a single server.

    Args:
        transport: Connection backend (stdio, in-process, etc.).
        server_name: Logical name for logging and tool-name prefixing.
        request_timeout: Seconds to wait for a JSON-RPC response.
        max_reconnect_attempts: How many times to retry after
            unexpected transport closure before giving up.
    """

    def __init__(
        self,
        transport: Transport,
        server_name: str,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT,
        max_reconnect_attempts: int = _MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self._transport = transport
        self._server_name = server_name
        self._request_timeout = request_timeout
        self._max_reconnect_attempts = max_reconnect_attempts

        # JSON-RPC bookkeeping
        self._next_id: int = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}

        # Read loop
        self._read_task: asyncio.Task[None] | None = None

        # State
        self._connected: bool = False
        self._reconnect_attempts: int = 0
        self._server_capabilities: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}
        self._closing: bool = False

        # Optional callback invoked after a successful reconnect.
        # Set by ExtensionManager to trigger bridge.sync_tools().
        self.on_reconnect: Callable[[], Awaitable[None]] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def server_name(self) -> str:
        """Logical name of this MCP server."""
        return self._server_name

    @property
    def is_connected(self) -> bool:
        """Whether the client has an active, initialised connection."""
        return self._connected

    @property
    def server_capabilities(self) -> dict[str, Any]:
        """Capabilities reported by the server during initialization."""
        return self._server_capabilities

    @property
    def transport(self) -> Transport:
        """The underlying transport instance (for diagnostics)."""
        return self._transport

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the transport and perform the MCP handshake.

        Raises:
            McpError: If the transport fails to connect or the
                handshake fails.
        """
        await self._transport.connect()
        self._read_task = asyncio.create_task(
            self._read_loop(), name=f"mcp-read-{self._server_name}"
        )
        try:
            await self._initialize()
        except Exception:
            await self.close()
            raise
        self._connected = True
        self._reconnect_attempts = 0
        logger.info("MCP server '%s' connected", self._server_name)

    async def close(self) -> None:
        """Gracefully shut down the transport and reject pending requests.

        Closes the transport, cancels the read loop, and fails all
        outstanding requests.  Idempotent.
        """
        self._closing = True
        self._connected = False

        # Cancel read loop
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        # Reject pending requests
        reject_all_pending(self._pending, "Client closing")

        # Close transport
        await self._transport.close()

        self._closing = False
        logger.debug("MCP server '%s' closed", self._server_name)

    async def handle_transport_closed(self, reason: str) -> None:
        """React to an unexpected transport closure.

        Called by the health monitor or read loop when the connection
        drops.  Marks the client as disconnected, rejects pending
        requests, and triggers reconnection.

        Args:
            reason: Human-readable description of the closure cause.
        """
        if self._closing or not self._connected:
            return
        logger.warning("MCP server '%s' transport closed: %s", self._server_name, reason)
        self._connected = False
        reject_all_pending(self._pending, reason)
        asyncio.create_task(self._attempt_reconnect())

    # ------------------------------------------------------------------
    # MCP protocol methods
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """Request the server's tool list.

        Returns:
            List of tool definition dicts (``name``, ``description``,
            ``inputSchema``).

        Raises:
            McpError: On protocol or transport errors.
        """
        result = await self._send_request("tools/list")
        return result.get("tools", [])

    async def list_resources(self) -> list[dict[str, Any]]:
        """Request the server's resource list.

        Returns:
            List of resource dicts (``uri``, ``name``,
            ``description?``, ``mimeType?``).

        Raises:
            McpError: On protocol or transport errors.
        """
        result = await self._send_request("resources/list")
        return result.get("resources", [])

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read a resource from the server.

        Args:
            uri: Resource URI to read.

        Returns:
            Raw result dict (``contents`` array with ``uri``,
            ``mimeType?``, ``text?``, ``blob?`` entries).

        Raises:
            McpError: On protocol or transport errors.
        """
        return await self._send_request("resources/read", {"uri": uri})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool on the server.

        Args:
            name: Original tool name (without ``mcp__`` prefix).
            arguments: Tool arguments dict.

        Returns:
            Raw result dict from the server (``content``, ``isError``).

        Raises:
            McpError: On protocol or transport errors.
        """
        return await self._send_request("tools/call", {"name": name, "arguments": arguments})

    # ------------------------------------------------------------------
    # MCP handshake
    # ------------------------------------------------------------------

    async def _initialize(self) -> None:
        """Perform the MCP initialize/initialized handshake.

        Raises:
            McpError: If the server rejects initialization.
        """
        result = await self._send_request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": _CLIENT_NAME,
                    "version": _CLIENT_VERSION,
                },
            },
        )

        self._server_capabilities = result.get("capabilities", {})
        self._server_info = result.get("serverInfo", {})

        # Send initialized notification (no response expected)
        await self._send_notification("notifications/initialized")

        logger.debug(
            "MCP server '%s' initialized — server: %s, capabilities: %s",
            self._server_name,
            self._server_info,
            list(self._server_capabilities.keys()),
        )

    # ------------------------------------------------------------------
    # JSON-RPC transport
    # ------------------------------------------------------------------

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response.

        Args:
            method: RPC method name.
            params: Optional parameters dict.

        Returns:
            The ``result`` field from the JSON-RPC response.

        Raises:
            McpError: On timeout, transport failure, or error response.
        """
        if not self._transport.is_connected:
            raise McpError(f"MCP server '{self._server_name}' not running")

        request_id = self._next_id
        self._next_id += 1

        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._transport.send(json.dumps(msg).encode())
        except TransportClosed as exc:
            self._pending.pop(request_id, None)
            raise McpError(f"MCP server '{self._server_name}' transport closed") from exc

        try:
            result = await asyncio.wait_for(future, timeout=self._request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise McpError(
                f"MCP request '{method}' to '{self._server_name}' "
                f"timed out after {self._request_timeout}s"
            ) from None

        return result

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected).

        Raises:
            McpError: If the transport is not connected.
        """
        if not self._transport.is_connected:
            raise McpError(f"MCP server '{self._server_name}' not running")

        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params

        try:
            await self._transport.send(json.dumps(msg).encode())
        except TransportClosed as exc:
            raise McpError(f"MCP server '{self._server_name}' transport closed") from exc

    # ------------------------------------------------------------------
    # Read loop
    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Drive the transport reader and trigger reconnect on closure.

        Blocks on ``transport.receive()`` in a loop.  When the
        transport signals closure (via :class:`TransportClosed`),
        delegates to :meth:`handle_transport_closed` for cleanup
        and reconnection.
        """
        try:
            while True:
                body = await self._transport.receive()
                self._dispatch_message(body)
        except TransportClosed:
            if not self._closing:
                await self.handle_transport_closed("Connection lost")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("MCP read loop error for '%s'", self._server_name)
            if not self._closing:
                await self.handle_transport_closed("Read loop error")

    def _dispatch_message(self, body: bytes) -> None:
        """Delegate to :func:`jsonrpc.dispatch_response`."""
        dispatch_response(body, self._pending, self._server_name)

    # ------------------------------------------------------------------
    # Reconnection
    # ------------------------------------------------------------------

    async def _attempt_reconnect(self) -> None:
        """Reconnect with exponential back-off after unexpected closure.

        Retries up to ``_max_reconnect_attempts`` times.  Each retry
        waits ``min(2^attempt, 60)`` seconds.
        """
        while self._reconnect_attempts < self._max_reconnect_attempts:
            self._reconnect_attempts += 1
            delay = min(2**self._reconnect_attempts, _MAX_RECONNECT_DELAY)

            logger.info(
                "MCP server '%s' disconnected — reconnecting in %ds (attempt %d/%d)",
                self._server_name,
                delay,
                self._reconnect_attempts,
                self._max_reconnect_attempts,
            )

            await asyncio.sleep(delay)

            try:
                # Close old transport state before reconnecting.
                # Reject all pending requests so callers get a clear error
                # rather than hanging indefinitely.
                await self._transport.close()
                for req_id, future in list(self._pending.items()):
                    if not future.done():
                        future.set_exception(
                            ConnectionError(f"MCP server '{self._server_name}' reconnecting")
                        )
                self._pending.clear()
                await self.connect()
                # Notify listener (e.g. bridge refreshes tool list)
                if self.on_reconnect:
                    try:
                        await self.on_reconnect()
                    except Exception:
                        logger.warning(
                            "on_reconnect callback failed for '%s'",
                            self._server_name,
                            exc_info=True,
                        )
                return  # Success
            except Exception:
                logger.warning(
                    "MCP reconnect failed for '%s' (attempt %d)",
                    self._server_name,
                    self._reconnect_attempts,
                )

        # All attempts exhausted — report stderr for diagnostics
        stderr_tail = ""
        from daemon.extensions.mcp.transport.stdio import StdioTransport

        if isinstance(self._transport, StdioTransport):
            stderr_tail = self._transport.stderr_tail

        logger.error(
            "MCP server '%s' — giving up after %d reconnect attempts. Stderr tail: %s",
            self._server_name,
            self._max_reconnect_attempts,
            stderr_tail or "(not stdio)",
        )
