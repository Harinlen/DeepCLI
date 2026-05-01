"""Transport behavior tests using a lightweight mock module_table.

The companion file ``test_session_ws.py`` exercises the real FastAPI
lifespan end-to-end (boot, ConnectionAuthenticator, full
token/password flow).  This file covers the three behaviors that
are hard to trigger through the real lifespan:

  1. ConnectionAuthenticator subsystem not loaded — new KeyError
     guard added to transport that was previously unprotected.
  2. Decode error recovery — a bad frame must produce an error frame
     and keep the connection alive, not kill it.
  3. on_disconnect lifecycle — called in every termination path
     (normal client close, auth failure path excluded).

Plus unit tests for the two pure-function helpers in the module.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kernel.connection_auth.context import AuthContext
from kernel.connection_auth.connection_authenticator import AuthError
from kernel.routes.flags import TransportFlags
from kernel.routes.session import (
    _MissingCredentials,
    _extract_credentials,
    _format_remote_addr,
    router,
)
from kernel.routes.stack import ProtocolError, ProtocolStack
from kernel.routes.stack.dummy import DummyCodec, DummyDispatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_ctx(credential_type: str = "token") -> AuthContext:
    return AuthContext(
        connection_id="test-conn",
        credential_type=credential_type,
        remote_addr="127.0.0.1:55555",
        authenticated_at=datetime.now(timezone.utc),
    )


def _module_table(
    *,
    missing_manager: bool = False,
    auth_error: bool = False,
    credential_type: str = "token",
) -> MagicMock:
    """Mock KernelModuleTable for lightweight transport tests."""
    mt = MagicMock()
    mt.flags.get_section.return_value = TransportFlags(stack="dummy")

    if missing_manager:
        mt.get.side_effect = KeyError("Subsystem not loaded: ConnectionAuthenticator")
    else:
        authenticator = AsyncMock()
        if auth_error:
            authenticator.authenticate.side_effect = AuthError()
        else:
            authenticator.authenticate.return_value = _auth_ctx(credential_type)
        mt.get.return_value = authenticator

    return mt


def _app(mt: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.module_table = mt
    return app


def _expect_4003(app: FastAPI, url: str) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(url) as ws:
            ws.receive_text()
    assert exc_info.value.code == 4003


# ---------------------------------------------------------------------------
# Unit: _extract_credentials
# ---------------------------------------------------------------------------


class TestExtractCredentials:
    def _qs(self, **kv: str):
        from starlette.datastructures import QueryParams

        return QueryParams("&".join(f"{k}={v}" for k, v in kv.items()))

    def test_token_only(self):
        cred, typ = _extract_credentials(self._qs(token="tok"))
        assert cred == "tok" and typ == "token"

    def test_password_only(self):
        cred, typ = _extract_credentials(self._qs(password="pass"))
        assert cred == "pass" and typ == "password"

    def test_token_wins_when_both_given(self):
        cred, typ = _extract_credentials(self._qs(token="tok", password="pass"))
        assert cred == "tok" and typ == "token"

    def test_neither_raises(self):
        with pytest.raises(_MissingCredentials):
            _extract_credentials(self._qs())

    def test_empty_token_treated_as_absent(self):
        with pytest.raises(_MissingCredentials):
            _extract_credentials(self._qs(token=""))

    def test_empty_password_treated_as_absent(self):
        with pytest.raises(_MissingCredentials):
            _extract_credentials(self._qs(password=""))


# ---------------------------------------------------------------------------
# Unit: _format_remote_addr
# ---------------------------------------------------------------------------


class TestFormatRemoteAddr:
    def test_normal_client(self):
        ws = MagicMock()
        ws.client.host = "127.0.0.1"
        ws.client.port = 54321
        assert _format_remote_addr(ws) == "127.0.0.1:54321"

    def test_none_client_returns_unknown(self):
        ws = MagicMock()
        ws.client = None
        assert _format_remote_addr(ws) == "unknown"


# ---------------------------------------------------------------------------
# ConnectionAuthenticator not loaded (new KeyError guard)
# ---------------------------------------------------------------------------


def test_connection_authenticator_not_loaded_closes_4003() -> None:
    """KeyError from module_table.get(ConnectionAuthenticator) → close(4003)."""
    _expect_4003(_app(_module_table(missing_manager=True)), "/session?token=any")


# ---------------------------------------------------------------------------
# Decode error recovery
# ---------------------------------------------------------------------------


class _FailOnKeyword(DummyCodec):
    """Raises ProtocolError for frames equal to ``"BADINPUT"``."""

    def decode(self, raw: str) -> str:
        if raw == "BADINPUT":
            raise ProtocolError("malformed frame")
        return raw

    def encode_error(self, error: ProtocolError) -> str:
        return json.dumps({"error": str(error)})


def _failing_stack() -> ProtocolStack:
    return ProtocolStack(codec=_FailOnKeyword(), dispatcher=DummyDispatcher())


def test_decode_error_sends_error_frame_and_keeps_connection_alive() -> None:
    """A ProtocolError from decode must not close the connection."""
    client = TestClient(_app(_module_table()), raise_server_exceptions=False)
    with patch("kernel.routes.session.create_stack", return_value=_failing_stack()):
        with client.websocket_connect("/session?token=valid") as ws:
            # Good frame → echoed normally
            ws.send_text("good")
            assert ws.receive_text() == "good"

            # Bad frame → error frame, connection stays open
            ws.send_text("BADINPUT")
            err = json.loads(ws.receive_text())
            assert "error" in err

            # Connection is still alive
            ws.send_text("still-here")
            assert ws.receive_text() == "still-here"


# ---------------------------------------------------------------------------
# on_disconnect lifecycle
# ---------------------------------------------------------------------------


class _TrackingDispatcher:
    """Records every on_disconnect call for assertion."""

    def __init__(self, log: list) -> None:
        self._log = log

    async def dispatch(self, msg: str, auth: Any) -> AsyncIterator[str]:
        yield msg

    async def on_disconnect(self, auth: Any) -> None:
        self._log.append(auth)


def _tracking_stack(log: list) -> ProtocolStack:
    return ProtocolStack(codec=DummyCodec(), dispatcher=_TrackingDispatcher(log))


def test_on_disconnect_called_after_client_disconnect() -> None:
    log: list = []
    client = TestClient(_app(_module_table()), raise_server_exceptions=False)
    with patch("kernel.routes.session.create_stack", return_value=_tracking_stack(log)):
        with client.websocket_connect("/session?token=valid") as ws:
            ws.send_text("ping")
            ws.receive_text()
        # __exit__ → client sends close → server finally block runs

    assert len(log) == 1
    assert log[0].credential_type == "token"


def test_on_disconnect_not_called_when_auth_fails() -> None:
    """on_disconnect must not fire if auth failed before stack was created."""
    log: list = []
    client = TestClient(_app(_module_table(auth_error=True)), raise_server_exceptions=False)
    with patch("kernel.routes.session.create_stack", return_value=_tracking_stack(log)):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/session?token=bad") as ws:
                ws.receive_text()

    # create_stack was never reached, TrackingDispatcher never instantiated
    assert len(log) == 0
