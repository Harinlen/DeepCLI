"""MCP transport layer — factory for transport instances.

Each transport type handles the mechanics of a single connection
to one MCP server.  ``create_transport()`` is the entry point
used by ``McpClient`` to obtain the right transport for a config.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kernel.mcp.transport.base import Transport

if TYPE_CHECKING:
    from kernel.mcp.config import ServerConfig

logger = logging.getLogger(__name__)


def create_transport(
    name: str,
    config: ServerConfig,
    *,
    auth_headers: dict[str, str] | None = None,
) -> Transport:
    """Instantiate the appropriate transport for *config*.

    Args:
        name: Server name (for log messages).
        config: Pydantic server config (discriminated by ``type``).
        auth_headers: Extra headers (e.g. ``Authorization: Bearer ...``)
            merged into the transport's headers for remote transports.
            These take precedence over config headers on conflict.

    Returns:
        An unconnected :class:`Transport` instance.

    Raises:
        ValueError: If the config type is unrecognised.
    """
    # Avoid circular import — config module imports nothing from transport.
    from kernel.mcp.config import (
        HTTPServerConfig,
        SSEServerConfig,
        StdioServerConfig,
        WebSocketServerConfig,
    )
    from kernel.mcp.transport.http import HTTPTransport
    from kernel.mcp.transport.sse import SSETransport
    from kernel.mcp.transport.stdio import StdioTransport
    from kernel.mcp.transport.ws import WebSocketTransport

    def _merge_headers(base: dict[str, str] | None) -> dict[str, str] | None:
        if not auth_headers and not base:
            return None
        merged = dict(base or {})
        if auth_headers:
            merged.update(auth_headers)
        return merged or None

    match config:
        case StdioServerConfig():
            return StdioTransport(
                command=config.command,
                args=config.args,
                env=config.env or None,
                # StdioTransport expands env vars internally.
                # auth_headers not applicable for stdio.
            )
        case SSEServerConfig():
            return SSETransport(
                url=config.url,
                headers=_merge_headers(config.headers),
                server_name=name,
            )
        case HTTPServerConfig():
            return HTTPTransport(
                url=config.url,
                headers=_merge_headers(config.headers),
                server_name=name,
            )
        case WebSocketServerConfig():
            return WebSocketTransport(
                url=config.url,
                headers=_merge_headers(config.headers),
                server_name=name,
            )
        case _:
            raise ValueError(f"unknown MCP server config type: {type(config).__name__}")
