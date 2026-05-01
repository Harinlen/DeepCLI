"""Tests for WebSocket endpoint."""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from daemon.engine.stream import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    UsageInfo,
)
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition

AUTH_TOKEN = "test-token-123"


# ------------------------------------------------------------------
# Fake provider
# ------------------------------------------------------------------


class FakeProvider(Provider):
    """Provider that returns a canned response."""

    name = "local"

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        yield ThinkingDelta(content="Let me think...")
        yield TextDelta(content="Hi!")
        yield StreamEnd(usage=UsageInfo(input_tokens=5, output_tokens=2))

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake", name="fake", provider="local")]


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _fake_from_config(config: Any) -> Any:
    """Build a registry with FakeProvider."""
    from daemon.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry._default_provider = "local"
    registry.register(FakeProvider())
    return registry


def _fake_load_config() -> Any:
    """Build an isolated RuntimeConfig with a single 'local' provider.

    Avoids reading the user's ~/.mustang/config.yaml during tests.
    """
    from daemon.config.defaults import apply_defaults
    from daemon.config.schema import SourceConfig

    return apply_defaults(SourceConfig())


@pytest.fixture
def client(tmp_path):
    """TestClient with patched provider and auth."""
    token_path = tmp_path / ".auth_token"
    with (
        patch("daemon.auth.AUTH_DIR", tmp_path),
        patch("daemon.auth.AUTH_TOKEN_PATH", token_path),
        patch("daemon.app.ensure_auth_token", return_value=AUTH_TOKEN),
        patch("daemon.app.load_config", side_effect=_fake_load_config),
        patch(
            "daemon.app.ProviderRegistry.from_config",
            side_effect=_fake_from_config,
        ),
    ):
        from daemon.app import create_app

        app = create_app()
        with TestClient(app) as c:
            yield c


def _consume_session_id(ws: Any) -> dict:
    """Read and validate the initial session_id message."""
    msg = ws.receive_json()
    assert msg["type"] == "session_id"
    assert "session_id" in msg
    return msg


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestWebSocket:
    """Tests for the /ws WebSocket endpoint."""

    def test_ws_session_id_on_connect(self, client: TestClient) -> None:
        """Connecting sends a session_id message immediately."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            msg = _consume_session_id(ws)
            assert len(msg["session_id"]) > 0

    def test_ws_chat_roundtrip(self, client: TestClient) -> None:
        """Send a user_message, receive thinking_delta + text_delta + end."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)

            ws.send_json({"type": "user_message", "content": "hello"})

            msg1 = ws.receive_json()
            assert msg1["type"] == "thinking_delta"
            assert msg1["content"] == "Let me think..."

            msg2 = ws.receive_json()
            assert msg2["type"] == "text_delta"
            assert msg2["content"] == "Hi!"

            msg3 = ws.receive_json()
            assert msg3["type"] == "end"

    def test_ws_invalid_token(self, client: TestClient) -> None:
        """Connection with wrong token is accepted then closed with 4001."""
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws?token=wrong") as ws:
                ws.receive_json()  # triggers the close frame
        assert exc_info.value.code == 4001

    def test_ws_no_token(self, client: TestClient) -> None:
        """Connection without token is accepted then closed with 4001."""
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
        assert exc_info.value.code == 4001

    def test_ws_empty_message(self, client: TestClient) -> None:
        """Empty content returns error."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "user_message", "content": ""})
            msg = ws.receive_json()
            assert msg["type"] == "error"

    def test_ws_unknown_type(self, client: TestClient) -> None:
        """Unknown message type returns error."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "bogus"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Unknown" in msg["message"]

    def test_ws_clear(self, client: TestClient) -> None:
        """Clear command resets conversation."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "clear"})
            msg = ws.receive_json()
            assert msg["type"] == "cleared"

    def test_ws_model_status(self, client: TestClient) -> None:
        """model_status returns the current provider + model."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "model_status"})
            msg = ws.receive_json()
            assert msg["type"] == "model_status_result"
            assert msg["provider_name"] == "local"
            assert msg["is_override"] is False
            assert "model" in msg
            assert "default_provider_name" in msg

    def test_ws_model_list(self, client: TestClient) -> None:
        """model_list returns all configured providers."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "model_list"})
            msg = ws.receive_json()
            assert msg["type"] == "model_list_result"
            assert msg["current"] == "local"
            assert isinstance(msg["providers"], list)
            assert len(msg["providers"]) >= 1
            names = [p["name"] for p in msg["providers"]]
            assert "local" in names

    def test_ws_model_switch_invalid(self, client: TestClient) -> None:
        """Switching to an unknown provider name fails gracefully."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "model_switch", "provider_name": "does-not-exist"})
            msg = ws.receive_json()
            assert msg["type"] == "model_switch_result"
            assert msg["ok"] is False
            assert "not configured" in msg["error"]
            assert "available" in msg

    def test_ws_model_switch_valid(self, client: TestClient) -> None:
        """Switching to an existing provider succeeds."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "model_switch", "provider_name": "local"})
            msg = ws.receive_json()
            assert msg["type"] == "model_switch_result"
            assert msg["ok"] is True
            assert msg["provider_name"] == "local"

            # model_status now reflects the override.
            ws.send_json({"type": "model_status"})
            s = ws.receive_json()
            assert s["is_override"] is True

    def test_ws_cost_query_empty(self, client: TestClient) -> None:
        """cost_query on a fresh session returns zeros."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "cost_query"})
            msg = ws.receive_json()
            assert msg["type"] == "cost_info"
            assert msg["total_input_tokens"] == 0
            assert msg["total_output_tokens"] == 0
            assert msg["model_usage"] == {}
            # Provider / current_model populated from session meta.
            assert "current_model" in msg
            assert "provider" in msg

    def test_ws_cost_query_after_chat(self, client: TestClient) -> None:
        """cost_query reflects accumulated usage after a chat turn."""
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws:
            _consume_session_id(ws)
            ws.send_json({"type": "user_message", "content": "hi"})
            # Drain stream until end
            while True:
                m = ws.receive_json()
                if m["type"] == "end":
                    break

            ws.send_json({"type": "cost_query"})
            msg = ws.receive_json()
            assert msg["type"] == "cost_info"
            # FakeProvider returns input=5, output=2
            assert msg["total_input_tokens"] == 5
            assert msg["total_output_tokens"] == 2
            # Per-model breakdown includes one entry keyed on effective model.
            assert len(msg["model_usage"]) == 1

    def test_ws_join_existing_session(self, client: TestClient) -> None:
        """Connecting with session_id joins the existing session."""
        # Create a session
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}") as ws1:
            sid_msg = _consume_session_id(ws1)
            session_id = sid_msg["session_id"]

        # Rejoin the same session
        with client.websocket_connect(f"/ws?token={AUTH_TOKEN}&session_id={session_id}") as ws2:
            sid_msg2 = _consume_session_id(ws2)
            assert sid_msg2["session_id"] == session_id

    def test_ws_nonexistent_session(self, client: TestClient) -> None:
        """Connecting with invalid session_id sends error then closes."""
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws?token={AUTH_TOKEN}&session_id=nonexistent") as ws:
                # First message is the error
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert "not found" in msg["message"].lower()
                # Then connection closes
                ws.receive_json()
        assert exc_info.value.code == 4004
