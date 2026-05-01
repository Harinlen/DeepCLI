"""Streamable HTTP transport — the MCP spec's successor to SSE.

Mirrors Claude Code's use of ``@modelcontextprotocol/sdk``
``StreamableHTTPClientTransport``: a single HTTP endpoint handles
both client→server requests (POST) and server→client push (SSE
responses on the same POST, or a separate GET stream).

Key differences from SSE transport:
- Single endpoint — no ``endpoint`` discovery step.
- POST responses may themselves be SSE streams (for streaming results).
- Supports server-initiated notifications via optional GET stream.

Timeout policy (matches CC):
- POST requests: 60 s for non-streaming, no limit for streaming.
- GET SSE stream: no read timeout (long-lived).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kernel.mcp.transport.base import Transport
from kernel.mcp.types import McpAuthError, TransportClosed

logger = logging.getLogger(__name__)

_POST_TIMEOUT: float = 60.0


class HTTPTransport(Transport):
    """Streamable HTTP transport for one MCP server.

    Args:
        url: The MCP HTTP endpoint URL.
        headers: Extra HTTP headers (auth tokens, etc.).
        server_name: For log messages.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        server_name: str = "http",
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._server_name = server_name

        self._connected = False
        self._receive_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._session_id: str | None = None
        self._client: Any = None  # httpx.AsyncClient
        self._sse_task: asyncio.Task[None] | None = None

    # ── Transport interface ─────────────────────────────────────────

    async def connect(self) -> None:
        """Initialize HTTP client.

        Unlike SSE, Streamable HTTP has no separate discovery step.
        The session is established on the first POST (``initialize``).
        """
        import httpx

        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(
                connect=10.0,
                read=None,  # streaming responses
                write=10.0,
                pool=10.0,
            ),
        )
        self._connected = True
        logger.debug("HTTPTransport[%s]: ready at %s", self._server_name, self._url)

    async def send(self, message: bytes) -> None:
        """POST a JSON-RPC frame and enqueue streamed response events."""
        if not self._connected or self._client is None:
            raise TransportClosed("HTTP transport not connected")

        import httpx

        send_headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._session_id:
            send_headers["Mcp-Session-Id"] = self._session_id

        try:
            resp = await self._client.post(
                self._url,
                content=message,
                headers=send_headers,
                timeout=_POST_TIMEOUT,
            )
            if resp.status_code == 401:
                raise McpAuthError(self._server_name)
            if resp.status_code == 404:
                # Session expired — server dropped session state.
                self._connected = False
                raise TransportClosed("session expired (404)")
            resp.raise_for_status()
        except (McpAuthError, TransportClosed):
            raise
        except httpx.TimeoutException as exc:
            raise TransportClosed(f"POST timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            self._connected = False
            raise TransportClosed(f"POST failed: {exc}") from exc

        # Capture session ID from server response.
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid

        # The response may be:
        # 1. A direct JSON body (single response).
        # 2. An SSE stream (text/event-stream) with multiple events.
        content_type = resp.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # SSE-encoded response — parse events and enqueue.
            self._parse_sse_body(resp.text)
        elif resp.text.strip():
            # Direct JSON response.
            self._receive_queue.put_nowait(resp.text.encode())

    async def receive(self) -> bytes:
        """Block until the next JSON-RPC frame arrives."""
        if not self._connected and self._receive_queue.empty():
            raise TransportClosed("HTTP transport not connected")

        try:
            data = await self._receive_queue.get()
        except asyncio.CancelledError:
            raise TransportClosed("receive cancelled")

        if not data:
            raise TransportClosed("HTTP transport closed")
        return data

    async def close(self) -> None:
        """Shut down HTTP client and background tasks."""
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

    # ── Internal helpers ────────────────────────────────────────────

    def _parse_sse_body(self, body: str) -> None:
        """Parse an SSE-encoded response body and enqueue data events."""
        data_lines: list[str] = []
        for line in body.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "":
                if data_lines:
                    payload = "\n".join(data_lines)
                    self._receive_queue.put_nowait(payload.encode())
                    data_lines = []
        # Flush trailing data without a final blank line.
        if data_lines:
            payload = "\n".join(data_lines)
            self._receive_queue.put_nowait(payload.encode())
