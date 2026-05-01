"""Handshake — abstract contract for connection initialisation.

Any protocol implementation that requires a multi-step initialisation
(capability negotiation, version selection, etc.) implements this
interface.  The ACP implementation lives in
:mod:`kernel.protocol.acp.handshake`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from kernel.protocol.interfaces.contracts.connection_context import (
    ConnectionContext,
)


@runtime_checkable
class Handshake(Protocol):
    """Protocol for the connection setup phase."""

    async def initialize(
        self,
        conn: ConnectionContext,
        params: BaseModel,
    ) -> BaseModel:
        """Handle the first message from a new client.

        Implementations MUST:

        * Validate / negotiate the protocol version.
        * Populate ``conn.client_info`` and
          ``conn.negotiated_capabilities`` from ``params``.
        * Set ``conn.initialized = True`` on success.
        * Return a response model that the codec can serialise.

        Parameters
        ----------
        conn:
            Mutable connection state; mutated in-place.
        params:
            The decoded initialise request params (concrete type
            depends on the protocol — ACP uses
            ``InitializeRequest``).
        """
        ...

    async def authenticate(
        self,
        conn: ConnectionContext,
        params: BaseModel,
    ) -> BaseModel:
        """Handle an optional ``authenticate`` request.

        In our architecture, real authentication happens at the
        transport layer before the protocol layer is entered.  This
        method exists so that clients that defensively send
        ``authenticate`` (as the ACP spec says they MAY) receive a
        graceful success response rather than ``Method not found``.

        Implementations MAY be a no-op that returns an empty response.
        """
        ...
