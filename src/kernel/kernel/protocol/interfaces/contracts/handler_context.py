"""Per-dispatch context passed into every SessionHandler call.

Kept deliberately minimal: the session layer can always reach
subsystems via ``app.state.module_table`` if it needs them.  Putting
every subsystem reference into HandlerContext would make it a god
object and make testing more expensive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kernel.protocol.interfaces.client_sender import ClientSender
    from kernel.protocol.interfaces.contracts.connection_context import (
        ConnectionContext,
    )


@dataclass(frozen=True)
class HandlerContext:
    """Immutable context injected into every ``SessionHandler`` method."""

    conn: ConnectionContext
    """Connection-level state (auth, negotiated capabilities, bound
    session id).  The session layer treats this as read-only."""

    sender: ClientSender
    """Capability injection: use this to send notifications or
    outgoing requests back to the connected client.  Scoped to the
    current connection — handlers never hold a WebSocket reference."""

    request_id: str | int | None
    """JSON-RPC ``id`` of the inbound request being handled.  ``None``
    for notifications.  Useful for log correlation across layers."""
