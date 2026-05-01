"""Per-connection mutable state maintained by the protocol layer.

Created when a WebSocket connection is accepted and progressively
filled in as the handshake proceeds:

1. ``auth`` is set by the transport layer before the protocol layer
   ever sees the connection.
2. ``initialized``, ``client_info``, and ``negotiated_capabilities``
   are filled in by the ``initialize`` handler.
3. ``bound_session_id`` is set by the ``session/new`` or
   ``session/load`` handler.

The dataclass is intentionally **not** frozen — the handshake mutates
it in place.  Only the protocol layer writes to it; the session layer
treats it as read-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from kernel.connection_auth import AuthContext


@dataclass
class ClientInfo:
    """Name and version of the connected client."""

    name: str
    title: str | None = None
    version: str | None = None


@dataclass
class ConnectionContext:
    """Mutable per-connection state owned by the protocol layer."""

    auth: AuthContext
    """Immutable identity produced by ``ConnectionAuthenticator``.
    Set once, never changed for the lifetime of the connection."""

    initialized: bool = False
    """``True`` after ``initialize`` has been successfully handled."""

    client_info: ClientInfo | None = None
    """Filled in during ``initialize`` from ``clientInfo``."""

    negotiated_capabilities: dict[str, Any] = field(default_factory=dict)
    """Client capabilities from ``clientCapabilities`` in ``initialize``.
    Stored for diagnostics only; we don't use them in one-period."""

    bound_session_id: str | None = None
    """Set after ``session/new`` or ``session/load`` succeeds."""
