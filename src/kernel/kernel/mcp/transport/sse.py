"""SSE transport — connect to a remote MCP server via HTTP Server-Sent Events.

Mirrors Claude Code's use of ``@modelcontextprotocol/sdk``
``SSEClientTransport``: an HTTP GET opens a long-lived SSE stream for
server→client messages, and an HTTP POST sends client→server requests
to an endpoint discovered from the first SSE ``endpoint`` event.

Timeout policy (matches CC):
- POST requests: 60 s (``wrapFetchWithTimeout`` equivalent).
- SSE stream: no read timeout (long-lived connection).
- Endpoint discovery: 30 s.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kernel.mcp.transport.base import Transport
from kernel.mcp.types import McpAuthError, TransportClosed

logger = logging.getLogger(__name__)

# Timeouts (seconds).
_POST_TIMEOUT: float = 60.0
_ENDPOINT_DISCOVERY_TIMEOUT: float = 30.0


class SSETransport(Transport):
    """Half-duplex SSE transport for one MCP server.

    Args:
        url: Base SSE endpoint URL (GET).
        headers: Extra HTTP headers (auth tokens, etc.).
        server_name: For log messages.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        server_name: str = "sse",
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._server_name = server_name

        self._post_url: str | None = None
        self._connected = False
        self._receive_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._sse_task: asyncio.Task[None] | None = None
        self._endpoint_ready = asyncio.Event()
        # Lazy import so the module loads even when httpx isn't installed
        # (dependency is declared in pyproject.toml).
        self._client: Any = None  # httpx.AsyncClient

    # ── Transport interface ─────────────────────────────────────────

    async def connect(self) -> None:
        """Open the SSE stream and discover the POST endpoint."""
        import httpx

        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(
                connect=10.0,
                read=None,  # SSE stream — no read timeout
                write=10.0,
                pool=10.0,
            ),
        )

        self._sse_task = asyncio.create_task(self._sse_loop(), name=f"mcp-sse-{self._server_name}")

        # Wait for the first ``endpoint`` event.
        try:
            await asyncio.wait_for(
                self._endpoint_ready.wait(),
                timeout=_ENDPOINT_DISCOVERY_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            await self.close()
            raise TransportClosed(
                f"SSE endpoint discovery timed out after {_ENDPOINT_DISCOVERY_TIMEOUT}s"
            ) from exc

        self._connected = True
        logger.debug(
            "SSETransport[%s]: connected, post_url=%s",
            self._server_name,
            self._post_url,
        )

    async def send(self, message: bytes) -> None:
        """POST a JSON-RPC frame to the server endpoint."""
        if not self._connected or self._post_url is None or self._client is None:
            raise TransportClosed("SSE transport not connected")

        import httpx

        try:
            resp = await self._client.post(
                self._post_url,
                content=message,
                headers={"Content-Type": "application/json"},
                timeout=_POST_TIMEOUT,
            )
            if resp.status_code == 401:
                raise McpAuthError(self._server_name)
            resp.raise_for_status()
        except McpAuthError:
            raise
        except httpx.TimeoutException as exc:
            raise TransportClosed(f"POST timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            self._connected = False
            raise TransportClosed(f"POST failed: {exc}") from exc

    async def receive(self) -> bytes:
        """Block until the next JSON-RPC frame from the SSE stream."""
        if not self._connected and self._receive_queue.empty():
            raise TransportClosed("SSE transport not connected")

        try:
            return await self._receive_queue.get()
        except asyncio.CancelledError:
            raise TransportClosed("receive cancelled")

    async def close(self) -> None:
        """Shut down SSE stream and HTTP client."""
        self._connected = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Internal: SSE stream reader ─────────────────────────────────

    async def _sse_loop(self) -> None:
        """Background task: read the SSE stream, dispatch events."""
        import httpx

        try:
            async with self._client.stream("GET", self._url) as resp:
                if resp.status_code == 401:
                    raise McpAuthError(self._server_name)
                resp.raise_for_status()

                event_type: str | None = None
                data_lines: list[str] = []

                async for raw_line in resp.aiter_lines():
                    line = raw_line.rstrip("\r\n")

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line == "":
                        # End of event — dispatch.
                        if event_type and data_lines:
                            data = "\n".join(data_lines)
                            self._handle_sse_event(event_type, data)
                        event_type = None
                        data_lines = []
                    # Ignore comment lines (starting with ':') and
                    # other non-standard fields.

        except McpAuthError:
            self._connected = False
            raise
        except asyncio.CancelledError:
            return
        except (httpx.HTTPError, OSError) as exc:
            logger.debug("SSETransport[%s]: stream ended: %s", self._server_name, exc)
        finally:
            self._connected = False
            # Wake up any blocked receive() call.
            await self._receive_queue.put(b"")  # sentinel

    def _handle_sse_event(self, event_type: str, data: str) -> None:
        """Route an SSE event by type.

        ``endpoint`` → store POST URL and unblock ``connect()``.
        ``message``  → enqueue raw bytes for ``receive()``.
        """
        if event_type == "endpoint":
            self._post_url = self._resolve_endpoint(data)
            self._endpoint_ready.set()
        elif event_type == "message":
            self._receive_queue.put_nowait(data.encode())
        else:
            logger.debug(
                "SSETransport[%s]: unknown event type %r",
                self._server_name,
                event_type,
            )

    def _resolve_endpoint(self, endpoint: str) -> str:
        """Resolve a possibly-relative endpoint URL against the base."""
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        # Relative path — resolve against base URL.
        from urllib.parse import urljoin

        return urljoin(self._url, endpoint)
