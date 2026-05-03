"""Unit tests for GatewayAdapter base class logic.

Tests cover:
- Permission reply interception (takes priority over normal messages).
- Session creation serialisation (no duplicate sessions from concurrent messages).
- Per-session lock is released before turn runs (no deadlock with permission reply).
- Empty reply is not forwarded to send().
- stop() rejects all pending permission futures.
- _chunk_text helper.
- _persist/_load peer_sessions roundtrip.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kernel.gateways.base import GatewayAdapter, InboundMessage, _YES_WORDS
from kernel.gateways.discord.adapter import _chunk_text
from kernel.gateways.manager import (
    GatewayManager,
    GatewayManagerConfig,
    _create_adapter,
)
from kernel.orchestrator.types import PermissionRequest, PermissionResponse
from kernel.protocol.acp.schemas.permission import (
    PermissionOption,
    RequestPermissionRequest,
    ToolCallUpdate,
)


# ---------------------------------------------------------------------------
# Concrete stub adapter
# ---------------------------------------------------------------------------


class _StubAdapter(GatewayAdapter):
    """Minimal concrete GatewayAdapter for testing base-class logic."""

    def __init__(self, module_table: Any) -> None:
        super().__init__(
            instance_id="test-stub",
            config={},
            module_table=module_table,
        )
        self.sent: list[tuple[str, str | None, str]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        await super().stop()

    async def send(self, peer_id: str, thread_id: str | None, text: str) -> None:
        self.sent.append((peer_id, thread_id, text))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def module_table() -> MagicMock:
    mt = MagicMock()

    # SessionManager mock
    session_mgr = MagicMock()
    session_mgr.create_for_gateway = AsyncMock(return_value="session-001")
    session_mgr.run_turn_for_gateway = AsyncMock(return_value="Hello!")

    # CommandManager mock
    cmd_mgr = MagicMock()
    cmd_mgr.lookup.return_value = None  # unknown command by default

    def _get(cls: type) -> Any:
        from kernel.session import SessionManager
        from kernel.commands import CommandManager

        if cls is SessionManager:
            return session_mgr
        if cls is CommandManager:
            return cmd_mgr
        raise KeyError(cls)

    mt.get.side_effect = _get
    return mt


@pytest.fixture
def adapter(module_table: MagicMock) -> _StubAdapter:
    return _StubAdapter(module_table)


def _msg(text: str = "hello", peer: str = "u1", thread: str | None = "ch1") -> InboundMessage:
    return InboundMessage(instance_id="test-stub", peer_id=peer, thread_id=thread, text=text)


# ---------------------------------------------------------------------------
# Permission interception
# ---------------------------------------------------------------------------


async def test_permission_reply_yes_resolves_future(adapter: _StubAdapter) -> None:
    key = ("u1", "ch1")
    fut: asyncio.Future[PermissionResponse] = asyncio.get_event_loop().create_future()
    adapter._pending_permissions[key] = fut

    await adapter._handle(_msg("yes"))

    assert fut.done()
    assert fut.result().decision == "allow_once"


@pytest.mark.parametrize("word", sorted(_YES_WORDS))
async def test_all_yes_words_resolve_allow(adapter: _StubAdapter, word: str) -> None:
    key = ("u1", "ch1")
    fut: asyncio.Future[PermissionResponse] = asyncio.get_event_loop().create_future()
    adapter._pending_permissions[key] = fut

    await adapter._handle(_msg(word))
    assert fut.result().decision == "allow_once"


async def test_permission_reply_no_resolves_reject(adapter: _StubAdapter) -> None:
    key = ("u1", "ch1")
    fut: asyncio.Future[PermissionResponse] = asyncio.get_event_loop().create_future()
    adapter._pending_permissions[key] = fut

    await adapter._handle(_msg("no"))

    assert fut.result().decision == "reject"


async def test_permission_reply_gibberish_resolves_reject(adapter: _StubAdapter) -> None:
    key = ("u1", "ch1")
    fut: asyncio.Future[PermissionResponse] = asyncio.get_event_loop().create_future()
    adapter._pending_permissions[key] = fut

    await adapter._handle(_msg("maybe later"))
    assert fut.result().decision == "reject"


async def test_permission_reply_does_not_start_turn(
    adapter: _StubAdapter, module_table: MagicMock
) -> None:
    """A permission reply must not trigger a new LLM turn."""
    from kernel.session import SessionManager

    key = ("u1", "ch1")
    fut: asyncio.Future[PermissionResponse] = asyncio.get_event_loop().create_future()
    adapter._pending_permissions[key] = fut

    await adapter._handle(_msg("yes"))

    module_table.get(SessionManager).run_turn_for_gateway.assert_not_called()


# ---------------------------------------------------------------------------
# Normal message flow
# ---------------------------------------------------------------------------


async def test_normal_message_creates_session_and_runs_turn(
    adapter: _StubAdapter, module_table: MagicMock
) -> None:
    from kernel.session import SessionManager

    await adapter._handle(_msg("hello world"))

    sm = module_table.get(SessionManager)
    sm.create_for_gateway.assert_called_once_with(instance_id="test-stub", peer_id="u1")
    sm.run_turn_for_gateway.assert_called_once()
    # Reply should be sent back.
    assert adapter.sent == [("u1", "ch1", "Hello!")]


async def test_empty_reply_is_not_sent(adapter: _StubAdapter, module_table: MagicMock) -> None:
    """Tool-only turns return '' — must not call send()."""
    from kernel.session import SessionManager

    module_table.get(SessionManager).run_turn_for_gateway = AsyncMock(return_value="")
    await adapter._handle(_msg("run tool"))
    assert adapter.sent == []


async def test_session_reused_across_messages(
    adapter: _StubAdapter, module_table: MagicMock
) -> None:
    from kernel.session import SessionManager

    await adapter._handle(_msg("first"))
    await adapter._handle(_msg("second"))

    # create_for_gateway called only once for the same (peer, thread).
    sm = module_table.get(SessionManager)
    assert sm.create_for_gateway.call_count == 1


async def test_router_backend_routes_platform_message_through_hub(
    adapter: _StubAdapter,
    module_table: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUSTANG_AGENT_PROMPT_BACKEND", "router")
    module_table.agent_hub_endpoint = "ws://127.0.0.1:9999"
    calls: list[dict[str, Any]] = []

    async def _route(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if kwargs["contract"] == "agent.session_new":
            return {"ok": True, "sessionId": "hub-session-1"}
        return {
            "ok": True,
            "updates": [
                {
                    "sessionId": "hub-session-1",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "Hub reply"},
                    },
                }
            ],
        }

    adapter._route_agent_contract_through_hub = AsyncMock(side_effect=_route)  # type: ignore[method-assign]

    await adapter._handle(
        InboundMessage(
            instance_id="test-stub",
            peer_id="u1",
            thread_id="ch1",
            text="hello via hub",
            raw={"id": "platform-message-1"},
        )
    )

    assert [call["contract"] for call in calls] == ["agent.session_new", "agent.prompt"]
    assert calls[1]["params"]["_meta"]["mustang.agent/clientTurnId"]
    assert adapter.sent == [("u1", "ch1", "Hub reply")]


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


async def test_unknown_command_sends_error(adapter: _StubAdapter) -> None:
    await adapter._handle(_msg("/foobar"))
    assert any("Unknown command" in text for _, _, text in adapter.sent)


# ---------------------------------------------------------------------------
# stop() cleans up pending permissions
# ---------------------------------------------------------------------------


async def test_stop_rejects_all_pending_permissions(adapter: _StubAdapter) -> None:
    loop = asyncio.get_running_loop()
    futs = [loop.create_future() for _ in range(3)]
    for i, f in enumerate(futs):
        adapter._pending_permissions[(f"u{i}", "ch1")] = f

    await adapter.stop()

    for f in futs:
        assert f.done()
        assert f.result().decision == "reject"
    assert len(adapter._pending_permissions) == 0


# ---------------------------------------------------------------------------
# Permission timeout
# ---------------------------------------------------------------------------


async def test_permission_waits_indefinitely_until_reply(adapter: _StubAdapter) -> None:
    """Permission callback waits for user reply without timeout."""
    cb = adapter._make_permission_callback("u1", "ch1")
    req = PermissionRequest(
        tool_use_id="t1",
        tool_name="bash",
        tool_title="Bash",
        input_summary="echo hi",
        risk_level="low",
    )
    # Start the callback — it will block waiting for a reply.
    task = asyncio.create_task(cb(req))
    await asyncio.sleep(0.05)  # let the prompt be sent

    # Prompt was sent to user.
    assert any("yes" in text.lower() for _, _, text in adapter.sent)

    # Simulate user reply — resolve the pending future.
    fut = adapter._pending_permissions.get(("u1", "ch1"))
    assert fut is not None
    fut.set_result(PermissionResponse(decision="allow"))

    result = await asyncio.wait_for(task, timeout=2.0)
    assert result.decision == "allow"


async def test_platform_permission_request_maps_allow_and_reject(adapter: _StubAdapter) -> None:
    params = RequestPermissionRequest(
        session_id="s-1",
        tool_call=ToolCallUpdate(
            tool_call_id="tool-1",
            title="Write file",
            kind="edit",
            input_summary="path: README.md",
        ),
        options=[PermissionOption(option_id="allow_once", name="Allow", kind="allow_once")],
        tool_input={"path": "README.md"},
    )
    msg = _msg("hello")

    task = asyncio.create_task(adapter._request_permission_over_platform(params, msg))
    await asyncio.sleep(0.05)

    assert adapter.sent[-1] == (
        "u1",
        "ch1",
        "**Permission required**: `Write file`\n"
        "path: README.md\n"
        "Reply **yes** to allow or **no** to deny.",
    )
    await adapter._handle(_msg("yes"))

    allowed = await asyncio.wait_for(task, timeout=2.0)
    assert allowed.outcome.outcome == "selected"
    assert allowed.outcome.option_id == "allow_once"

    task = asyncio.create_task(adapter._request_permission_over_platform(params, msg))
    await asyncio.sleep(0.05)
    await adapter._handle(_msg("no"))
    rejected = await asyncio.wait_for(task, timeout=2.0)
    assert rejected.outcome.outcome == "cancelled"


async def test_handle_hub_client_request_rejects_unknown_method(adapter: _StubAdapter) -> None:
    from kernel.agents import HubFrame, HubFrameType

    frame = HubFrame(
        frame_id="f-1",
        frame_type=HubFrameType.REQUEST,
        contract="client.request",
        payload={"method": "client/unknown", "params": {}},
    )

    response = await adapter._handle_hub_client_request(frame, _msg("hello"))

    assert response.frame_type is HubFrameType.RESPONSE
    assert response.correlation_id == "f-1"
    assert response.payload["ok"] is False
    assert response.payload["error"] == "RuntimeError"


def test_client_turn_id_prefers_platform_raw_id(adapter: _StubAdapter) -> None:
    first = adapter._client_turn_id_for_message(
        InboundMessage("test-stub", "u1", "ch1", "hello", raw={"id": "m-1"})
    )
    second = adapter._client_turn_id_for_message(
        InboundMessage("test-stub", "u1", "ch1", "hello again", raw={"id": "m-1"})
    )
    other_thread = adapter._client_turn_id_for_message(
        InboundMessage("test-stub", "u1", "ch2", "hello", raw={"id": "m-1"})
    )

    assert first == second
    assert first != other_thread


# ---------------------------------------------------------------------------
# _chunk_text (Discord adapter helper)
# ---------------------------------------------------------------------------


def test_chunk_text_short_unchanged() -> None:
    assert _chunk_text("hello", 2000) == ["hello"]


def test_chunk_text_splits_at_newline() -> None:
    long_line = "a" * 1500
    text = long_line + "\n" + long_line
    chunks = _chunk_text(text, 2000)
    assert len(chunks) == 2
    assert all(len(c) <= 2000 for c in chunks)


def test_chunk_text_force_splits_long_line() -> None:
    text = "x" * 5000
    chunks = _chunk_text(text, 2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_text_empty() -> None:
    # Empty string returns a single empty chunk (or at least doesn't crash).
    result = _chunk_text("", 2000)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Peer-session persistence roundtrip
# ---------------------------------------------------------------------------


async def test_peer_sessions_persist_and_reload(adapter: _StubAdapter, tmp_path: Path) -> None:
    with patch.object(adapter, "_peer_sessions_path", return_value=tmp_path / "peer_sessions.json"):
        adapter._peer_sessions = {("user1", "chan1"): "sess-aaa", ("user2", None): "sess-bbb"}
        await adapter._persist_peer_sessions()

        adapter2 = _StubAdapter(adapter._module_table)
        with patch.object(
            adapter2, "_peer_sessions_path", return_value=tmp_path / "peer_sessions.json"
        ):
            await adapter2._load_peer_sessions()

        assert adapter2._peer_sessions.get(("user1", "chan1")) == "sess-aaa"
        assert adapter2._peer_sessions.get(("user2", None)) == "sess-bbb"


# ---------------------------------------------------------------------------
# GatewayManager
# ---------------------------------------------------------------------------


def test_gateway_manager_config_returns_extra_adapter_entries() -> None:
    cfg = GatewayManagerConfig.model_validate(
        {"discord-main": {"type": "discord", "token": "secret"}}
    )

    assert cfg.adapter_entries() == {
        "discord-main": {"type": "discord", "token": "secret"}
    }


def test_create_adapter_rejects_unknown_adapter_type(module_table: MagicMock) -> None:
    with pytest.raises(ValueError, match="Unknown gateway adapter type 'irc'"):
        _create_adapter(
            adapter_type="irc",
            instance_id="chat",
            config={"type": "irc"},
            module_table=module_table,
        )


async def test_gateway_manager_startup_skips_missing_type_and_failed_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    class _GoodManagerAdapter(_StubAdapter):
        def __init__(self, instance_id: str, config: dict[str, Any], module_table: Any) -> None:
            super().__init__(module_table)
            self._instance_id = instance_id

        async def start(self) -> None:
            started.append(self._instance_id)

    class _BadManagerAdapter(_GoodManagerAdapter):
        async def start(self) -> None:
            raise RuntimeError("bad token")

    mt = MagicMock()
    section = MagicMock()
    section.get.return_value = GatewayManagerConfig.model_validate(
        {
            "missing": {},
            "bad": {"type": "bad"},
            "good": {"type": "good"},
        }
    )
    mt.config.get_section.return_value = section
    monkeypatch.setattr(
        "kernel.gateways.manager._build_adapter_registry",
        lambda: {"bad": _BadManagerAdapter, "good": _GoodManagerAdapter},
    )
    manager = GatewayManager(mt)

    await manager.startup()

    assert started == ["good"]
    assert list(manager._adapters) == ["good"]


async def test_gateway_manager_shutdown_and_channel_delivery_tolerate_edges(
    module_table: MagicMock,
) -> None:
    manager = GatewayManager(module_table)
    good = _StubAdapter(module_table)
    bad = _StubAdapter(module_table)
    bad.stop = AsyncMock(side_effect=RuntimeError("stop failed"))  # type: ignore[method-assign]
    manager._adapters = {"good": good, "bad": bad}

    await manager.send_to_channel("good", "channel-1", "hello")
    assert good.sent == [("cron-delivery", "channel-1", "hello")]
    with pytest.raises(KeyError, match="No running gateway adapter"):
        await manager.send_to_channel("missing", "channel-1", "hello")

    await manager.shutdown()
    assert manager._adapters == {}


async def test_gateway_manager_webhook_routes_when_adapter_supports_it(
    module_table: MagicMock,
) -> None:
    class _WebhookAdapter(_StubAdapter):
        def __init__(self, mt: Any) -> None:
            super().__init__(mt)
            self.payloads: list[dict[str, Any]] = []

        async def handle_webhook(self, payload: dict[str, Any]) -> None:
            self.payloads.append(payload)

    manager = GatewayManager(module_table)
    adapter = _WebhookAdapter(module_table)
    manager._adapters = {"web": adapter}

    await manager.handle_webhook("web", {"event": "message"})

    assert adapter.payloads == [{"event": "message"}]

    manager._adapters["plain"] = _StubAdapter(module_table)
    await manager.handle_webhook("plain", {"event": "ignored"})

    with pytest.raises(KeyError, match="No running gateway adapter"):
        await manager.handle_webhook("missing", {})
