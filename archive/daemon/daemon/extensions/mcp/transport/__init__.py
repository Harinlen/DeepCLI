"""MCP transport layer — pluggable connection backends.

Provides a :class:`Transport` ABC and concrete implementations for
different wire protocols.  The factory function :func:`create_transport`
selects the right implementation based on ``McpServerEntry.type``.

Supported transports:

- ``stdio``: subprocess stdin/stdout with LSP Content-Length framing
- ``inprocess``: asyncio.Queue pair for same-process MCP servers
- ``sse``: HTTP/SSE for remote MCP servers
- ``ws``: WebSocket for remote MCP servers
"""

from __future__ import annotations

from daemon.extensions.mcp.config import McpServerEntry
from daemon.extensions.mcp.transport.base import Transport, TransportClosed
from daemon.extensions.mcp.transport.inprocess import InProcessTransport
from daemon.extensions.mcp.transport.sse import SseTransport
from daemon.extensions.mcp.transport.stdio import StdioTransport
from daemon.extensions.mcp.transport.websocket import WebSocketTransport


class UnsupportedTransport(Exception):
    """Raised when a server entry specifies an unknown transport type."""


def create_transport(entry: McpServerEntry) -> Transport:
    """Build the appropriate transport for a server configuration.

    Args:
        entry: Server config with ``type`` field selecting the
            transport backend.

    Returns:
        A ready-to-connect :class:`Transport` instance.

    Raises:
        UnsupportedTransport: If ``entry.type`` is not recognised.
    """
    match entry.type:
        case "stdio":
            return StdioTransport(entry)
        case "inprocess":
            return _create_inprocess_transport(entry)
        case "sse":
            return SseTransport(
                url=entry.url,
                headers=entry.headers or None,
                name=entry.name,
            )
        case "ws":
            return WebSocketTransport(
                url=entry.url,
                headers=entry.headers or None,
                name=entry.name,
            )
        case _:
            raise UnsupportedTransport(f"Transport type '{entry.type}' is not supported")


def _create_inprocess_transport(entry: McpServerEntry) -> InProcessTransport:
    """Build an in-process transport by importing the server class.

    The server module and class are specified in the config entry's
    ``module`` and ``class_name`` fields.

    Raises:
        UnsupportedTransport: If the module/class cannot be loaded.
    """
    import importlib

    if not entry.module or not entry.class_name:
        raise UnsupportedTransport(
            f"In-process server '{entry.name}' requires 'module' and 'class' fields"
        )

    try:
        mod = importlib.import_module(entry.module)
        server_cls = getattr(mod, entry.class_name)
    except (ImportError, AttributeError) as exc:
        raise UnsupportedTransport(
            f"Cannot load in-process server '{entry.name}': "
            f"{entry.module}.{entry.class_name} — {exc}"
        ) from exc

    return InProcessTransport(server_factory=server_cls, name=entry.name)


__all__ = [
    "InProcessTransport",
    "SseTransport",
    "StdioTransport",
    "Transport",
    "TransportClosed",
    "UnsupportedTransport",
    "WebSocketTransport",
    "create_transport",
]
