"""ACP implementation of the Handshake contract.

Handles the two protocol-layer methods that the session layer never
sees: ``initialize`` and ``authenticate``.

``initialize``
--------------
* Negotiates protocol version (ACP spec: if agent supports requested
  version, return it; otherwise return highest supported version).
* Fills in :class:`~kernel.protocol.interfaces.contracts.connection_context.ConnectionContext`.
* Sets ``conn.initialized = True``.
* Returns a full ``InitializeResponse`` with our capabilities and info.

``authenticate``
----------------
All real authentication happens at the transport layer before the
protocol layer is entered.  ``authMethods: []`` in our
``InitializeResponse`` signals to clients that no protocol-level
authentication is needed.  If a client sends ``authenticate`` anyway
(defensive implementation), we return an empty success rather than
``Method not found`` (which would look like a non-compliant agent).
"""

from __future__ import annotations

import logging

import kernel
from kernel.protocol.acp.namespaces import MUSTANG_EXTENSION_METHODS
from kernel.protocol.acp.schemas.initialize import (
    AcpAgentCapabilities,
    AcpImplementation,
    AcpMcpCapabilities,
    AcpPromptCapabilities,
    AcpSessionCapabilities,
    AuthenticateRequest,
    AuthenticateResponse,
    InitializeRequest,
    InitializeResponse,
)
from kernel.protocol.interfaces.contracts.connection_context import (
    ClientInfo,
    ConnectionContext,
)

logger = logging.getLogger(__name__)

# ACP protocol version we support.  One-period: only v1.
_SUPPORTED_VERSION = 1


class AcpHandshake:
    """Concrete :class:`~kernel.protocol.interfaces.handshake.Handshake`
    implementation for the ACP protocol."""

    async def initialize(
        self,
        conn: ConnectionContext,
        params: InitializeRequest,
    ) -> InitializeResponse:
        """Negotiate version, fill connection context, return capabilities."""
        # Version negotiation (ACP spec):
        # - Client sends the highest version it supports.
        # - If we support it, echo it back.
        # - If we don't support it, return our highest supported version.
        negotiated = (
            params.protocol_version
            if params.protocol_version <= _SUPPORTED_VERSION
            else _SUPPORTED_VERSION
        )

        # Populate the mutable connection context.
        if params.client_info is not None:
            conn.client_info = ClientInfo(
                name=params.client_info.name,
                title=params.client_info.title,
                version=params.client_info.version,
            )
        conn.negotiated_capabilities = params.client_capabilities.model_dump()
        conn.initialized = True

        logger.info(
            "conn=%s initialized client=%s protocol_version=%d",
            conn.auth.connection_id,
            params.client_info.name if params.client_info else "unknown",
            negotiated,
        )

        return InitializeResponse(
            protocol_version=negotiated,
            agent_capabilities=AcpAgentCapabilities(
                load_session=True,
                prompt_capabilities=AcpPromptCapabilities(
                    image=True,
                    audio=False,
                    embedded_context=True,
                ),
                mcp_capabilities=AcpMcpCapabilities(
                    http=True,
                    sse=False,
                ),
                session_capabilities=AcpSessionCapabilities(
                    list={},
                    close={},
                    resume={},
                ),
                meta={
                    "mustang.agent/extensions": sorted(MUSTANG_EXTENSION_METHODS),
                    "mustang.agent/version": 1,
                },
            ),
            agent_info=AcpImplementation(
                name="mustang-kernel",
                title="DeepCLI",
                version=kernel.__version__,
            ),
            auth_methods=[],
        )

    async def authenticate(
        self,
        conn: ConnectionContext,
        params: AuthenticateRequest,
    ) -> AuthenticateResponse:
        """No-op: transport-layer auth already succeeded.

        Return empty success so a defensively-coded client that sends
        ``authenticate`` after ``initialize`` doesn't get
        ``Method not found``.
        """
        logger.debug(
            "conn=%s authenticate noop (transport-layer auth already passed)",
            conn.auth.connection_id,
        )
        return AuthenticateResponse()
