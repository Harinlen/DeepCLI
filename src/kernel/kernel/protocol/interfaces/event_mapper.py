"""EventMapper — abstract contract for orchestrator-event translation.

The Orchestrator emits native events (text delta, tool call start,
permission request, …) as it processes a prompt turn.  The protocol
layer must translate each event into one or more outbound messages
that the client understands.

Each protocol implementation provides a concrete ``EventMapper`` that
knows about its own wire format.  The session layer calls the mapper
via ``ctx.sender`` — it never reaches into ACP schemas directly.

One-period note
---------------
The Orchestrator subsystem is not yet implemented.  The event type
used here is a placeholder ``dict``; it will be replaced with a
proper ``OrchestratorEvent`` union type once that subsystem lands.
The mapper interface is intentionally minimal so the swap is a
one-line change.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kernel.protocol.interfaces.client_sender import ClientSender


@runtime_checkable
class EventMapper(Protocol):
    """Translate a single Orchestrator event into outbound client messages."""

    async def map(
        self,
        event: Any,
        sender: ClientSender,
        session_id: str,
    ) -> None:
        """Map ``event`` and push the corresponding message(s) via ``sender``.

        Parameters
        ----------
        event:
            A native Orchestrator event object.  The concrete type is
            protocol-specific (the ACP mapper knows about ACP update
            variants; a future protocol mapper would use its own types).
        sender:
            The outbound channel for the current connection.
        session_id:
            Passed into update notifications that require it.
        """
        ...
