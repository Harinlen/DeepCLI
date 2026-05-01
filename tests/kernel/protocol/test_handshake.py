"""Unit tests for AcpHandshake."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kernel.connection_auth.context import AuthContext
from kernel.protocol.acp.handshake import AcpHandshake
from kernel.protocol.acp.schemas.initialize import (
    AcpClientCapabilities,
    AcpImplementation,
    AuthenticateRequest,
    InitializeRequest,
)
from kernel.protocol.interfaces.contracts.connection_context import (
    ConnectionContext,
)


def _make_conn() -> ConnectionContext:
    auth = AuthContext(
        connection_id="test-conn-001",
        credential_type="token",
        remote_addr="127.0.0.1:9999",
        authenticated_at=datetime.now(timezone.utc),
    )
    return ConnectionContext(auth=auth)


@pytest.fixture
def handshake() -> AcpHandshake:
    return AcpHandshake()


@pytest.fixture
def conn() -> ConnectionContext:
    return _make_conn()


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


class TestInitialize:
    @pytest.mark.anyio
    async def test_sets_initialized(self, handshake: AcpHandshake, conn: ConnectionContext) -> None:
        params = InitializeRequest(protocol_version=1)
        await handshake.initialize(conn, params)
        assert conn.initialized is True

    @pytest.mark.anyio
    async def test_version_negotiation_exact(
        self, handshake: AcpHandshake, conn: ConnectionContext
    ) -> None:
        params = InitializeRequest(protocol_version=1)
        response = await handshake.initialize(conn, params)
        assert response.protocol_version == 1

    @pytest.mark.anyio
    async def test_version_negotiation_client_higher(
        self, handshake: AcpHandshake, conn: ConnectionContext
    ) -> None:
        """Client requests v99 — agent doesn't support it, returns v1."""
        params = InitializeRequest(protocol_version=99)
        response = await handshake.initialize(conn, params)
        assert response.protocol_version == 1

    @pytest.mark.anyio
    async def test_client_info_stored(
        self, handshake: AcpHandshake, conn: ConnectionContext
    ) -> None:
        params = InitializeRequest(
            protocol_version=1,
            client_info=AcpImplementation(name="test-client", title="Test Client", version="2.0"),
        )
        await handshake.initialize(conn, params)
        assert conn.client_info is not None
        assert conn.client_info.name == "test-client"
        assert conn.client_info.version == "2.0"

    @pytest.mark.anyio
    async def test_capabilities_stored(
        self, handshake: AcpHandshake, conn: ConnectionContext
    ) -> None:
        params = InitializeRequest(
            protocol_version=1,
            client_capabilities=AcpClientCapabilities(terminal=True),
        )
        await handshake.initialize(conn, params)
        assert conn.negotiated_capabilities.get("terminal") is True

    @pytest.mark.anyio
    async def test_agent_capabilities_in_response(
        self, handshake: AcpHandshake, conn: ConnectionContext
    ) -> None:
        params = InitializeRequest(protocol_version=1)
        response = await handshake.initialize(conn, params)
        caps = response.agent_capabilities
        assert caps.load_session is True
        assert caps.prompt_capabilities.image is True
        assert caps.mcp_capabilities.http is True
        assert caps.session_capabilities.list == {}

    @pytest.mark.anyio
    async def test_auth_methods_empty(
        self, handshake: AcpHandshake, conn: ConnectionContext
    ) -> None:
        params = InitializeRequest(protocol_version=1)
        response = await handshake.initialize(conn, params)
        assert response.auth_methods == []

    @pytest.mark.anyio
    async def test_agent_info_present(
        self, handshake: AcpHandshake, conn: ConnectionContext
    ) -> None:
        params = InitializeRequest(protocol_version=1)
        response = await handshake.initialize(conn, params)
        assert response.agent_info is not None
        assert response.agent_info.name == "mustang-kernel"


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


class TestAuthenticate:
    @pytest.mark.anyio
    async def test_noop_returns_empty(
        self, handshake: AcpHandshake, conn: ConnectionContext
    ) -> None:
        conn.initialized = True
        params = AuthenticateRequest(method_id="token")
        response = await handshake.authenticate(conn, params)
        # Empty response — no fields required
        assert response is not None
