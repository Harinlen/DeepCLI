"""McpClient — transport-agnostic MCP protocol session.

Mirrors Claude Code's use of ``@modelcontextprotocol/sdk`` ``Client``:
performs the MCP ``initialize`` / ``initialized`` handshake, exposes
typed helpers for ``tools/list``, ``tools/call``, ``resources/list``,
``resources/read``, and manages the JSON-RPC request/response
correlation in a background read loop.

Reconnection with exponential back-off is built in — callers set
``on_reconnect`` to refresh derived state (e.g. tool lists) after a
successful reconnect.

Connection-level functions (``connect_to_server``,
``reconnect_server``) live at module scope so ``MCPManager`` can
call them without reaching into the client internals.
"""

from __future__ import annotations

import asyncio
import orjson
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from kernel.mcp.jsonrpc import dispatch_response, reject_all_pending
from kernel.mcp.transport.base import Transport
from kernel.mcp.types import (
    McpAuthError,
    McpError,
    McpToolCallError,
    McpToolDef,
    McpToolResult,
    McpResourceDef,
    McpResourceResult,
    TransportClosed,
)

logger = logging.getLogger(__name__)

# MCP protocol version we advertise during handshake.
_PROTOCOL_VERSION = "2024-11-05"

# Default timeout (seconds) for MCP RPC requests (initialize, ping,
# resources/list, etc.).  Tool calls pass ``_NO_TIMEOUT`` because
# execution time is unbounded and each tool carries its own deadline.
_REQUEST_TIMEOUT: float = 30.0
_NO_TIMEOUT = object()  # sentinel: explicitly disable timeout
_MAX_RECONNECT_ATTEMPTS: int = 5
_MAX_RECONNECT_BACKOFF: float = 60.0


class McpClient:
    """JSON-RPC session over an MCP transport.

    Args:
        transport: An unconnected :class:`Transport` instance.
        server_name: Identifier used in log messages.
        request_timeout: Default per-request timeout.
        max_reconnect_attempts: How many times to retry on disconnect.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        server_name: str,
        request_timeout: float = _REQUEST_TIMEOUT,
        max_reconnect_attempts: int = _MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self._transport = transport
        self._server_name = server_name
        self._request_timeout = request_timeout
        self._max_reconnect_attempts = max_reconnect_attempts

        self._next_id: int = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._read_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._connected = False
        self._closing = False

        self._capabilities: dict[str, Any] = {}
        self._server_info: dict[str, Any] | None = None
        self._instructions: str | None = None

        #: Async callback invoked after a successful reconnect.
        #: MCPManager sets this so ToolManager can refresh tools.
        self.on_reconnect: Callable[[], Awaitable[None]] | None = None

        #: Async callback invoked when a 401 auth error is detected
        #: during the read loop reconnect path.  MCPManager sets this
        #: so it can transition the server to NeedsAuth state.
        self.on_auth_required: Callable[[], Awaitable[None]] | None = None

    # ── Lifecycle ───────────────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        """Open transport and perform MCP handshake.

        Returns:
            Server capabilities dict from the ``initialize`` response.

        Raises:
            McpError: If the handshake fails.
            TransportClosed: If the transport cannot connect.
        """
        await self._transport.connect()
        self._connected = True

        # Start background read loop before handshake so the response
        # can be dispatched.
        self._read_task = asyncio.create_task(
            self._read_loop(), name=f"mcp-read-{self._server_name}"
        )

        try:
            # MCP initialize request.
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mustang", "version": "0.1.0"},
                },
            )
        except BaseException:
            # Handshake failed — clean up the read task and transport
            # so no background tasks leak.
            await self.close()
            raise

        self._capabilities = result.get("capabilities", {})
        self._server_info = result.get("serverInfo")
        self._instructions = result.get("instructions")

        # Send initialized notification (no response expected).
        await self._notify("initialized", {})

        logger.info(
            "McpClient[%s]: handshake complete, capabilities=%s",
            self._server_name,
            list(self._capabilities.keys()),
        )
        return self._capabilities

    async def close(self) -> None:
        """Shut down transport and fail pending requests.

        Idempotent — safe to call on an already-closed client.
        """
        if self._closing:
            return
        self._closing = True
        self._connected = False

        reject_all_pending(self._pending, f"client closing ({self._server_name})")

        # Cancel any in-flight reconnect attempt first — it may be
        # sleeping or mid-handshake and would otherwise spawn a new
        # read loop after we clean up.
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        await self._transport.close()
        self._closing = False

    # ── Protocol methods ────────────────────────────────────────────

    async def list_tools(self) -> list[McpToolDef]:
        """Request ``tools/list``.

        Returns:
            List of tool definitions from the server.
        """
        result = await self._request("tools/list", {})
        raw_tools = result.get("tools", [])
        return [
            McpToolDef(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in raw_tools
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> McpToolResult:
        """Request ``tools/call``.

        Args:
            name: Tool name (server-local, not prefixed).
            arguments: Tool input dict.
            timeout: Override default tool-call timeout.

        Returns:
            Tool result with content blocks.

        Raises:
            McpToolCallError: If the server flags ``isError: true``.
            McpError: On protocol-level failure.
        """
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout if timeout is not None else _NO_TIMEOUT,
        )

        is_error = result.get("isError", False)
        content = result.get("content", [])
        meta = result.get("_meta")

        if is_error:
            # Extract human-readable message from content blocks.
            msg_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            raise McpToolCallError(
                name,
                " ".join(msg_parts) or "tool returned an error",
                meta=meta,
            )

        return McpToolResult(content=content, is_error=False, meta=meta)

    async def list_resources(self) -> list[McpResourceDef]:
        """Request ``resources/list``."""
        result = await self._request("resources/list", {})
        raw = result.get("resources", [])
        return [
            McpResourceDef(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType"),
            )
            for r in raw
        ]

    async def read_resource(self, uri: str) -> McpResourceResult:
        """Request ``resources/read``."""
        result = await self._request("resources/read", {"uri": uri})
        return McpResourceResult(contents=result.get("contents", []))

    # ── Properties ──────────────────────────────────────────────────

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def capabilities(self) -> dict[str, Any]:
        return self._capabilities

    @property
    def server_info(self) -> dict[str, Any] | None:
        return self._server_info

    @property
    def instructions(self) -> str | None:
        return self._instructions

    @property
    def transport(self) -> Transport:
        return self._transport

    # ── Internal: JSON-RPC request / notification ───────────────────

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None | object = None,
    ) -> Any:
        """Send a JSON-RPC request and await the response.

        Args:
            method: JSON-RPC method name.
            params: Method parameters.
            timeout: Per-request timeout override.  ``None`` → use the
                instance default; ``_NO_TIMEOUT`` → wait forever.

        Returns:
            The ``result`` field from the JSON-RPC response.

        Raises:
            McpError: On error response or timeout.
            TransportClosed: If the transport dies mid-request.
        """
        request_id = self._next_id
        self._next_id += 1

        msg = orjson.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._transport.send(msg)
        except TransportClosed:
            self._pending.pop(request_id, None)
            raise

        if timeout is _NO_TIMEOUT:
            return await future

        # After the _NO_TIMEOUT check, timeout narrows to float | None.
        effective_timeout: float | None = (
            timeout if isinstance(timeout, (int, float)) else self._request_timeout
        )
        try:
            return await asyncio.wait_for(future, timeout=effective_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise McpError(
                f"{method} timed out after {effective_timeout}s",
                code=None,
            )

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg = orjson.dumps(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )
        await self._transport.send(msg)

    # ── Internal: background read loop ──────────────────────────────

    async def _read_loop(self) -> None:
        """Continuously read from transport and dispatch responses."""
        try:
            while self._connected:
                raw = await self._transport.receive()
                if not raw:
                    # Empty bytes = sentinel from transport (e.g. SSE close).
                    break
                dispatch_response(raw, self._pending, self._server_name)
        except TransportClosed as exc:
            logger.debug(
                "McpClient[%s]: transport closed: %s",
                self._server_name,
                exc,
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("McpClient[%s]: read loop crashed", self._server_name)
        finally:
            if not self._closing:
                self._connected = False
                reject_all_pending(
                    self._pending,
                    f"transport closed ({self._server_name})",
                )
                # Attempt reconnection in background.
                self._reconnect_task = asyncio.create_task(
                    self._attempt_reconnect(),
                    name=f"mcp-reconnect-{self._server_name}",
                )

    # ── Internal: reconnection ──────────────────────────────────────

    async def _attempt_reconnect(self) -> None:
        """Try to re-establish the connection with exponential back-off.

        Mirrors CC's reconnection pattern: ``min(2^attempt, 60)`` seconds
        between retries, up to ``max_reconnect_attempts``.

        Cancelled cleanly by ``close()`` during shutdown.
        """
        for attempt in range(1, self._max_reconnect_attempts + 1):
            if self._closing:
                return

            backoff = min(2.0**attempt, _MAX_RECONNECT_BACKOFF)
            logger.info(
                "McpClient[%s]: reconnect attempt %d/%d in %.0fs",
                self._server_name,
                attempt,
                self._max_reconnect_attempts,
                backoff,
            )
            await asyncio.sleep(backoff)

            if self._closing:
                return

            try:
                await self._transport.close()
                await self._transport.connect()
                self._connected = True

                # Restart read loop.
                self._read_task = asyncio.create_task(
                    self._read_loop(),
                    name=f"mcp-read-{self._server_name}",
                )

                # Re-handshake.
                result = await self._request(
                    "initialize",
                    {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "mustang", "version": "0.1.0"},
                    },
                )
                self._capabilities = result.get("capabilities", {})
                self._server_info = result.get("serverInfo")
                await self._notify("initialized", {})

                logger.info(
                    "McpClient[%s]: reconnected on attempt %d",
                    self._server_name,
                    attempt,
                )

                if self.on_reconnect is not None:
                    await self.on_reconnect()
                return

            except McpAuthError:
                # 401 won't be fixed by retrying — signal auth required
                # and stop the reconnect loop.
                logger.info(
                    "McpClient[%s]: server requires OAuth — stopping reconnect",
                    self._server_name,
                )
                self._connected = False
                if self.on_auth_required is not None:
                    await self.on_auth_required()
                return
            except (McpError, TransportClosed, OSError) as exc:
                logger.warning(
                    "McpClient[%s]: reconnect attempt %d failed: %s",
                    self._server_name,
                    attempt,
                    exc,
                )
                self._connected = False

        logger.error(
            "McpClient[%s]: gave up after %d reconnect attempts",
            self._server_name,
            self._max_reconnect_attempts,
        )
