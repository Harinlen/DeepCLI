"""SSE transport — MCP over HTTP Server-Sent Events.

Implements the MCP SSE transport specification:

1. Client opens a GET connection to the SSE endpoint.
2. Server sends an ``endpoint`` event containing the URL for
   JSON-RPC POST requests.
3. Client POSTs JSON-RPC messages to that URL.
4. Server streams JSON-RPC responses as SSE ``message`` events.

This is a half-duplex transport: requests go via POST, responses
come via the SSE stream.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from daemon.extensions.mcp.transport.base import Transport, TransportClosed

logger = logging.getLogger(__name__)

# How long to wait for the initial endpoint event after connecting.
_ENDPOINT_DISCOVERY_TIMEOUT = 15.0

# HTTP timeouts for the SSE stream and POST requests.
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
_POST_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


class SseTransport(Transport):
    """MCP transport over HTTP/SSE.

    Connects to a remote MCP server that implements the SSE
    transport specification.  The server URL must point to the
    SSE endpoint (GET); the POST endpoint is discovered from the
    first ``endpoint`` event in the stream.

    Args:
        url: SSE endpoint URL (e.g. ``https://mcp.example.com/sse``).
        headers: Additional HTTP headers (e.g. Authorization).
        name: Logical name for logging.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        name: str = "sse",
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._name = name

        self._client: httpx.AsyncClient | None = None
        self._event_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._sse_task: asyncio.Task[None] | None = None
        self._endpoint_url: str | None = None
        self._connected: bool = False
        self._endpoint_ready: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Transport interface
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the SSE stream and discover the POST endpoint.

        Raises:
            TransportClosed: If the endpoint is not discovered within
                the timeout.
        """
        self._event_queue = asyncio.Queue()
        self._endpoint_ready = asyncio.Event()
        self._endpoint_url = None

        self._client = httpx.AsyncClient(
            timeout=_STREAM_TIMEOUT,
            headers=self._headers,
        )

        self._sse_task = asyncio.create_task(self._sse_listen(), name=f"mcp-sse-{self._name}")

        # Wait for the endpoint event
        try:
            await asyncio.wait_for(
                self._endpoint_ready.wait(),
                timeout=_ENDPOINT_DISCOVERY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await self.close()
            raise TransportClosed(
                f"SSE endpoint discovery timed out after "
                f"{_ENDPOINT_DISCOVERY_TIMEOUT}s for '{self._name}'"
            ) from None

        # The event was set but endpoint might not have been discovered
        # (e.g. stream errored before sending the endpoint event).
        if not self._endpoint_url:
            await self.close()
            raise TransportClosed(
                f"SSE stream closed without sending endpoint event for '{self._name}'"
            )

        self._connected = True
        logger.debug(
            "SSE transport connected for '%s' — POST endpoint: %s",
            self._name,
            self._endpoint_url,
        )

    async def send(self, message: bytes) -> None:
        """POST a JSON-RPC message to the server's endpoint.

        Args:
            message: Serialized JSON-RPC payload.

        Raises:
            TransportClosed: If the transport is not connected or
                the POST fails.
        """
        if not self._connected or not self._client or not self._endpoint_url:
            raise TransportClosed("SSE transport not connected")

        try:
            resp = await self._client.post(
                self._endpoint_url,
                content=message,
                headers={"Content-Type": "application/json"},
                timeout=_POST_TIMEOUT,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TransportClosed(f"SSE POST failed for '{self._name}': {exc}") from exc

    async def receive(self) -> bytes:
        """Wait for the next JSON-RPC response from the SSE stream.

        Returns:
            Raw JSON-RPC message bytes.

        Raises:
            TransportClosed: If the stream ends or the task dies.
        """
        if not self._connected:
            raise TransportClosed("SSE transport not connected")

        try:
            return await self._event_queue.get()
        except asyncio.CancelledError:
            raise TransportClosed("SSE receive cancelled") from None

    async def close(self) -> None:
        """Close the SSE stream and HTTP client.  Idempotent."""
        self._connected = False

        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            self._sse_task = None

        if self._client:
            await self._client.aclose()
            self._client = None

        logger.debug("SSE transport closed for '%s'", self._name)

    @property
    def is_connected(self) -> bool:
        """True if connected and SSE stream is alive."""
        if not self._connected:
            return False
        if self._sse_task and self._sse_task.done():
            self._connected = False
            return False
        return True

    # ------------------------------------------------------------------
    # SSE stream reader
    # ------------------------------------------------------------------

    async def _sse_listen(self) -> None:
        """Read the SSE stream, parse events, and enqueue responses.

        Handles two event types:
        - ``endpoint``: contains the POST URL (first event)
        - ``message``: contains a JSON-RPC response body
        """
        assert self._client is not None
        try:
            async with self._client.stream(
                "GET",
                self._url,
                headers={"Accept": "text/event-stream"},
            ) as response:
                response.raise_for_status()
                event_type = ""
                data_lines: list[str] = []

                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif not line.strip():
                        # Empty line = end of event
                        if data_lines:
                            data = "\n".join(data_lines)
                            self._handle_sse_event(event_type, data)
                            data_lines = []
                            event_type = ""

        except asyncio.CancelledError:
            pass
        except httpx.HTTPError:
            logger.warning("SSE stream error for '%s'", self._name, exc_info=True)
        finally:
            # Signal that the stream has ended
            if not self._endpoint_ready.is_set():
                self._endpoint_ready.set()  # Unblock connect() if waiting

    def _handle_sse_event(self, event_type: str, data: str) -> None:
        """Dispatch a parsed SSE event.

        Args:
            event_type: The SSE event type (``endpoint`` or ``message``).
            data: The event data payload.
        """
        if event_type == "endpoint":
            # Resolve relative URLs against the base
            if data.startswith("/"):
                from urllib.parse import urljoin

                self._endpoint_url = urljoin(self._url, data)
            else:
                self._endpoint_url = data
            self._endpoint_ready.set()
        elif event_type == "message" or not event_type:
            # Default event type is "message" per SSE spec
            self._event_queue.put_nowait(data.encode())


__all__ = ["SseTransport"]
