"""End-to-end tests for AcpSessionHandler.

Tests the full dispatch path: raw JSON-RPC string → codec.decode →
dispatcher.dispatch → codec.encode → JSON-RPC response string.

The SessionManager subsystem is replaced by a ``FakeSessionHandler``
so tests run without a real kernel process.
"""

from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.agents.access.security.context import AuthContext
from kernel.core.protocol.acp.namespaces import AcpMethod, MustangMethod
from kernel.core.protocol.acp.codec import AcpCodec
from kernel.core.protocol.acp.session_handler import AcpSessionHandler
from kernel.core.protocol.acp.schemas.permission import (
    PermissionOutcomeSelected,
    RequestPermissionResponse,
)
from kernel.core.protocol.interfaces.contracts.list_sessions_result import (
    ListSessionsResult,
)
from kernel.core.protocol.interfaces.contracts.delete_session_result import DeleteSessionResult
from kernel.core.protocol.interfaces.contracts.close_session_result import CloseSessionResult
from kernel.core.protocol.interfaces.contracts.new_session_result import (
    NewSessionResult,
)
from kernel.core.protocol.interfaces.contracts.resume_session_result import ResumeSessionResult
from kernel.core.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.core.protocol.interfaces.contracts.execution_result import ExecutionResult
from kernel.agent_hub.contracts import HubFrame, HubFrameType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth(conn_id: str = "conn-e2e-test") -> AuthContext:
    return AuthContext(
        connection_id=conn_id,
        credential_type="token",
        remote_addr="127.0.0.1:1234",
        authenticated_at=datetime.now(timezone.utc),
    )


def _make_module_table(session_handler: Any) -> MagicMock:
    """Return a minimal module_table mock that returns ``session_handler``."""

    mt = MagicMock()
    mt.get.return_value = session_handler
    return mt


async def _collect(aiter) -> list:
    return [item async for item in aiter]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def codec() -> AcpCodec:
    return AcpCodec()


@pytest.fixture
def fake_session_handler():
    """Minimal ``SessionHandler`` that satisfies the Protocol."""
    handler = MagicMock()
    handler.new = AsyncMock(return_value=NewSessionResult(session_id="sess-001"))
    handler.list = AsyncMock(return_value=ListSessionsResult(sessions=[], next_cursor=None))
    handler.prompt = AsyncMock(return_value=PromptResult(stop_reason="end_turn"))
    handler.cancel = AsyncMock(return_value=None)
    handler.execute_shell = AsyncMock(return_value=ExecutionResult(exit_code=0))
    handler.execute_python = AsyncMock(return_value=ExecutionResult(exit_code=0))
    handler.cancel_execution = AsyncMock(return_value=None)
    handler.delete_session = AsyncMock(return_value=DeleteSessionResult(deleted=True))
    handler.resume_session = AsyncMock(return_value=ResumeSessionResult(replayed=False))
    handler.close_session = AsyncMock(return_value=CloseSessionResult())
    return handler


@pytest.fixture
def dispatcher(fake_session_handler) -> AcpSessionHandler:
    mt = _make_module_table(fake_session_handler)
    return AcpSessionHandler(mt)


@pytest.fixture
def auth() -> AuthContext:
    return _make_auth()


# ---------------------------------------------------------------------------
# Full encode→dispatch→decode round-trips
# ---------------------------------------------------------------------------


async def _round_trip(
    codec: AcpCodec,
    dispatcher: AcpSessionHandler,
    auth: AuthContext,
    raw_in: str,
) -> list[dict]:
    """Decode → dispatch → encode, return list of parsed response frames."""
    msg = codec.decode(raw_in)
    frames = []
    async for out in dispatcher.dispatch(msg, auth):
        frames.append(json.loads(codec.encode(out)))
    return frames


def _initialize_frame(conn_id: str = "conn-e2e-test") -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientInfo": {"name": "test-client", "title": "Test"},
            },
        }
    )


class TestInitializeFlow:
    @pytest.mark.anyio
    async def test_initialize_returns_capabilities(
        self, codec: AcpCodec, dispatcher: AcpSessionHandler, auth: AuthContext
    ) -> None:
        frames = await _round_trip(codec, dispatcher, auth, _initialize_frame())
        assert len(frames) == 1
        result = frames[0]["result"]
        # ACP wire format uses camelCase
        assert result["protocolVersion"] == 1
        assert result["agentCapabilities"]["loadSession"] is True
        assert result["authMethods"] == []

    @pytest.mark.anyio
    async def test_initialize_twice_returns_error(
        self, codec: AcpCodec, dispatcher: AcpSessionHandler, auth: AuthContext
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        frames = await _round_trip(codec, dispatcher, auth, _initialize_frame())
        assert frames[0]["error"]["code"] == -32600

    @pytest.mark.anyio
    async def test_session_new_before_initialize_returns_error(
        self, codec: AcpCodec, dispatcher: AcpSessionHandler, auth: AuthContext
    ) -> None:
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/new",
                "params": {"cwd": "/tmp", "mcpServers": []},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["error"]["code"] == -32600
        assert "initialize" in frames[0]["error"]["message"]


class TestSessionNewFlow:
    @pytest.mark.anyio
    async def test_session_new_returns_session_id(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/new",
                "params": {"cwd": "/home/user/project", "mcpServers": []},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert len(frames) == 1
        assert frames[0]["result"]["sessionId"] == "sess-001"
        fake_session_handler.new.assert_called_once()

    @pytest.mark.anyio
    async def test_session_new_passes_cwd(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                "params": {"cwd": "/workspace", "mcpServers": []},
            }
        )
        await _round_trip(codec, dispatcher, auth, raw)
        call_params = fake_session_handler.new.call_args[0][1]
        assert call_params.cwd == "/workspace"


class TestRuntimeClientRequestTunnel:
    @pytest.mark.anyio
    async def test_session_update_is_forwarded_to_access_client(self, dispatcher) -> None:
        class _Sender:
            def __init__(self) -> None:
                self.notifications = []

            async def notify(self, method, params):
                self.notifications.append((method, params))

        sender = _Sender()
        response = await dispatcher._handle_hub_client_request(
            HubFrame(
                frame_id="runtime-client-update-1",
                frame_type=HubFrameType.REQUEST,
                contract="client.request",
                payload={
                    "method": "session/update",
                    "params": {
                        "sessionId": "sess-001",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "hello"},
                        },
                    },
                },
            ),
            sender,
        )

        assert response.payload["ok"] is True
        assert sender.notifications[0][0] == "session/update"
        assert sender.notifications[0][1].session_id == "sess-001"
        assert sender.notifications[0][1].update.content.text == "hello"

    @pytest.mark.anyio
    async def test_permission_request_is_forwarded_to_access_client(self, dispatcher) -> None:
        class _Sender:
            async def request(self, method, params, *, result_type, timeout=None):
                assert method == "session/request_permission"
                assert params.session_id == "sess-001"
                assert params.tool_call.tool_call_id == "tool-1"
                return RequestPermissionResponse(
                    outcome=PermissionOutcomeSelected(option_id="allow_once")
                )

        response = await dispatcher._handle_hub_client_request(
            HubFrame(
                frame_id="runtime-client-request-1",
                frame_type=HubFrameType.REQUEST,
                contract="client.request",
                payload={
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "sess-001",
                        "toolCall": {"toolCallId": "tool-1"},
                        "options": [
                            {
                                "optionId": "allow_once",
                                "name": "Allow once",
                                "kind": "allow_once",
                            }
                        ],
                    },
                },
            ),
            _Sender(),
        )

        assert response.payload["ok"] is True
        assert response.payload["result"]["outcome"]["optionId"] == "allow_once"


class TestSessionListFlow:
    @pytest.mark.anyio
    async def test_session_list_returns_empty(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/list",
                "params": {},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["result"]["sessions"] == []


class TestSessionDeleteFlow:
    @pytest.mark.anyio
    async def test_session_delete_returns_deleted(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": MustangMethod.SESSION_DELETE,
                "params": {"sessionId": "sess-001", "force": True},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["result"]["deleted"] is True
        fake_session_handler.delete_session.assert_called_once()


class TestSessionResumeCloseFlow:
    @pytest.mark.anyio
    async def test_session_resume_returns_without_replay(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": AcpMethod.SESSION_RESUME,
                "params": {"sessionId": "sess-001", "cwd": "/tmp"},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["result"] == {"configOptions": []}
        fake_session_handler.resume_session.assert_called_once()

    @pytest.mark.anyio
    async def test_session_close_returns_empty_result(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": AcpMethod.SESSION_CLOSE,
                "params": {"sessionId": "sess-001"},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["result"] == {}
        fake_session_handler.close_session.assert_called_once()


class TestCancelRequestFlow:
    @pytest.mark.anyio
    async def test_cancel_request_cancels_in_flight_request(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        async def _slow_prompt(*_args: Any, **_kwargs: Any) -> PromptResult:
            await asyncio.sleep(5)
            return PromptResult(stop_reason="end_turn")

        fake_session_handler.prompt = AsyncMock(side_effect=_slow_prompt)
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        prompt_raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 33,
                "method": "session/prompt",
                "params": {"sessionId": "sess-001", "prompt": []},
            }
        )

        prompt_task = asyncio.create_task(_round_trip(codec, dispatcher, auth, prompt_raw))
        await asyncio.sleep(0.05)
        cancel_raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "$/cancel_request",
                "params": {"requestId": 33},
            }
        )
        cancel_frames = await _round_trip(codec, dispatcher, auth, cancel_raw)
        frames = await prompt_task

        assert cancel_frames == []
        assert frames[0]["error"]["code"] == -32800


class TestUserReplFlow:
    @pytest.mark.anyio
    async def test_execute_shell_returns_exit_code(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": MustangMethod.SESSION_EXECUTE_SHELL,
                "params": {"sessionId": "sess-001", "command": "echo hi"},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["result"]["exitCode"] == 0
        fake_session_handler.execute_shell.assert_called_once()

    @pytest.mark.anyio
    async def test_execute_python_returns_exit_code(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": MustangMethod.SESSION_EXECUTE_PYTHON,
                "params": {"sessionId": "sess-001", "code": "1 + 1"},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["result"]["exitCode"] == 0
        fake_session_handler.execute_python.assert_called_once()
        # nextCursor is None → excluded by exclude_none=True
        assert "nextCursor" not in frames[0]["result"]


class TestUnknownMethod:
    @pytest.mark.anyio
    async def test_unknown_method_returns_method_not_found(
        self, codec: AcpCodec, dispatcher: AcpSessionHandler, auth: AuthContext
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "totally/unknown",
                "params": {},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["error"]["code"] == -32601


class TestNotificationFlow:
    @pytest.mark.anyio
    async def test_cancel_notification_no_response(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        """session/cancel is a notification — must produce no response frame."""
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": AcpMethod.SESSION_CANCEL,
                "params": {"sessionId": "sess-001"},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames == []
        fake_session_handler.cancel.assert_called_once()

    @pytest.mark.anyio
    async def test_cancel_execution_notification_no_response(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": MustangMethod.SESSION_CANCEL_EXECUTION,
                "params": {"sessionId": "sess-001", "kind": "any"},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames == []
        fake_session_handler.cancel_execution.assert_called_once()

    @pytest.mark.anyio
    async def test_unknown_notification_silently_ignored(
        self, codec: AcpCodec, dispatcher: AcpSessionHandler, auth: AuthContext
    ) -> None:
        """Unknown notifications MUST be silently ignored (JSON-RPC 2.0 spec)."""
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "$/some_future_notification",
                "params": {},
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames == []


class TestDisconnect:
    @pytest.mark.anyio
    async def test_on_disconnect_cleans_up(
        self, dispatcher: AcpSessionHandler, auth: AuthContext
    ) -> None:
        # Trigger connection registration by decoding a frame.
        codec = AcpCodec()
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        assert auth.connection_id in dispatcher._connections
        await dispatcher.on_disconnect(auth)
        assert auth.connection_id not in dispatcher._connections

    @pytest.mark.anyio
    async def test_on_disconnect_idempotent(
        self, dispatcher: AcpSessionHandler, auth: AuthContext
    ) -> None:
        """Calling on_disconnect twice must not raise."""
        await dispatcher.on_disconnect(auth)
        await dispatcher.on_disconnect(auth)


class TestNotificationErrorHandling:
    @pytest.mark.anyio
    async def test_notification_handler_exception_does_not_close_connection(
        self,
        codec: AcpCodec,
        dispatcher: AcpSessionHandler,
        auth: AuthContext,
        fake_session_handler,
    ) -> None:
        """Notification errors must be swallowed — never propagate to transport."""
        fake_session_handler.cancel = AsyncMock(side_effect=RuntimeError("boom"))
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "session/cancel",
                "params": {"sessionId": "sess-001"},
            }
        )
        # Must not raise, must return no frames.
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames == []

        # Connection still usable — subsequent request works.
        raw2 = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "session/list",
                "params": {},
            }
        )
        frames2 = await _round_trip(codec, dispatcher, auth, raw2)
        assert len(frames2) == 1


class TestHubRoutingRetries:
    @pytest.mark.anyio
    async def test_runtime_not_registered_is_retried(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_session_handler,
        auth: AuthContext,
    ) -> None:
        mt = _make_module_table(fake_session_handler)
        mt.agent_hub_endpoint = "ws://hub.test"
        dispatcher = AcpSessionHandler(mt)
        monkeypatch.setattr(
            "kernel.core.protocol.acp.session_handler._HUB_RUNTIME_RETRY_DELAY_SECONDS",
            0,
        )
        calls = 0

        async def fake_request_hub(_endpoint: str, frame: HubFrame) -> HubFrame:
            nonlocal calls
            calls += 1
            if calls < 3:
                return HubFrame(
                    frame_id="hub-response",
                    frame_type=HubFrameType.RESPONSE,
                    contract=frame.contract,
                    payload={"ok": False, "error": "runtime_not_registered"},
                )
            return HubFrame(
                frame_id="hub-response",
                frame_type=HubFrameType.RESPONSE,
                contract=frame.contract,
                payload={"ok": True, "value": "ready"},
            )

        monkeypatch.setattr("kernel.agent_hub.request_hub", fake_request_hub)
        conn, _sender = dispatcher._get_or_create_connection(auth)
        payload = await dispatcher._route_agent_contract_through_hub(
            contract="agent.resume",
            params={"sessionId": "sess-001", "cwd": "/tmp"},
            session_id="sess-001",
            conn=conn,
        )

        assert calls == 3
        assert payload["value"] == "ready"


class TestInvalidParams:
    @pytest.mark.anyio
    async def test_invalid_params_returns_32602(
        self, codec: AcpCodec, dispatcher: AcpSessionHandler, auth: AuthContext
    ) -> None:
        await _round_trip(codec, dispatcher, auth, _initialize_frame())
        # session/new requires cwd; omit it to trigger validation error.
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "session/new",
                "params": {"mcpServers": []},  # missing cwd
            }
        )
        frames = await _round_trip(codec, dispatcher, auth, raw)
        assert frames[0]["error"]["code"] == -32602
