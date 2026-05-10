from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.core.protocol.acp.schemas.updates import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ConfigOptionUpdate,
    SessionUpdateNotification,
    ToolCallStart,
    ToolCallUpdateNotification,
    UsageUpdate,
    UserMessageChunk,
)
from kernel.agents.mustang.sessions.client_stream.replay import SessionReplayMixin
from kernel.agents.mustang.sessions.events import (
    AgentMessageEvent,
    ConfigOptionChangedEvent,
    ConversationMessageEvent,
    PermissionRequestEvent,
    ToolCallEvent,
    ToolCallUpdateEvent,
    TurnCompletedEvent,
    UserMessageEvent,
)
from kernel.agents.mustang.sessions.runtime.state import Session


class _Store:
    def __init__(self) -> None:
        self.spilled: dict[tuple[str, str], str] = {}

    def read_spilled(self, session_id: str, result_hash: str) -> str:
        return self.spilled[(session_id, result_hash)]


class _Replay(SessionReplayMixin):
    def __init__(self, store: _Store) -> None:
        self._store = store


def _session(tmp_path: Path) -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id="s-1",
        cwd=tmp_path,
        created_at=now,
        updated_at=now,
        title=None,
        git_branch=None,
        mode_id="default",
        config_options={},
        mcp_servers=[],
        orchestrator=None,  # type: ignore[arg-type]
    )


def _base(tmp_path: Path) -> dict[str, Any]:
    return {
        "event_id": "ev-1",
        "timestamp": datetime.now(timezone.utc),
        "session_id": "s-1",
        "cwd": str(tmp_path),
    }


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.sender.notify = AsyncMock()
    return ctx


@pytest.mark.anyio
async def test_replay_user_message_skips_malformed_blocks(tmp_path: Path) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()

    await replay._replay_event(
        ctx,
        _session(tmp_path),
        UserMessageEvent(
            **_base(tmp_path),
            content=[
                {"type": "text", "text": "hello"},
                {"type": "text"},
                {"type": "image", "data": "..."},
            ],
        ),
    )

    ctx.sender.notify.assert_called_once()
    notification = ctx.sender.notify.call_args.args[1]
    assert isinstance(notification, SessionUpdateNotification)
    assert isinstance(notification.update, UserMessageChunk)
    assert notification.update.content.text == "hello"


@pytest.mark.anyio
async def test_replay_agent_message_emits_one_chunk_per_text_block(tmp_path: Path) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()

    await replay._replay_event(
        ctx,
        _session(tmp_path),
        AgentMessageEvent(
            **_base(tmp_path),
            content=[
                {"type": "text", "text": "hello"},
                {"type": "resource_link", "uri": "file:///tmp/a"},
                {"type": "text", "text": " world"},
            ],
        ),
    )

    notifications = [call.args[1] for call in ctx.sender.notify.call_args_list]
    assert [n.update.content.text for n in notifications] == ["hello", " world"]
    assert all(isinstance(n.update, AgentMessageChunk) for n in notifications)


@pytest.mark.anyio
async def test_replay_events_falls_back_to_conversation_messages(tmp_path: Path) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()
    base = _base(tmp_path)

    await replay._replay_events(
        ctx,
        _session(tmp_path),
        [
            ConversationMessageEvent(
                **{**base, "event_id": "ev-user"},
                message={"role": "user", "content": [{"type": "text", "text": "question"}]},
            ),
            ConversationMessageEvent(
                **{**base, "event_id": "ev-assistant"},
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "reasoning", "signature": ""},
                        {"type": "text", "text": "answer"},
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Bash",
                            "input": {"command": "pwd"},
                        },
                    ],
                },
            ),
        ],
    )

    updates = [call.args[1].update for call in ctx.sender.notify.call_args_list]
    assert isinstance(updates[0], UserMessageChunk)
    assert updates[0].content.text == "question"
    assert isinstance(updates[1], AgentThoughtChunk)
    assert updates[1].content.text == "reasoning"
    assert isinstance(updates[2], AgentMessageChunk)
    assert updates[2].content.text == "answer"
    assert isinstance(updates[3], ToolCallStart)
    assert updates[3].tool_call_id == "tool-1"


@pytest.mark.anyio
async def test_replay_events_prefers_explicit_ui_transcript(tmp_path: Path) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()
    base = _base(tmp_path)

    await replay._replay_events(
        ctx,
        _session(tmp_path),
        [
            ConversationMessageEvent(
                **{**base, "event_id": "ev-conv"},
                message={"role": "assistant", "content": [{"type": "text", "text": "ui"}]},
            ),
            AgentMessageEvent(
                **{**base, "event_id": "ev-ui"},
                content=[{"type": "text", "text": "ui"}],
            ),
        ],
    )

    updates = [call.args[1].update for call in ctx.sender.notify.call_args_list]
    assert len(updates) == 1
    assert isinstance(updates[0], AgentMessageChunk)
    assert updates[0].content.text == "ui"


@pytest.mark.anyio
async def test_replay_events_recovers_conversation_when_ui_agent_event_is_empty(
    tmp_path: Path,
) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()
    base = _base(tmp_path)

    await replay._replay_events(
        ctx,
        _session(tmp_path),
        [
            ConversationMessageEvent(
                **{**base, "event_id": "ev-conv"},
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "conversation answer"}],
                },
            ),
            AgentMessageEvent(
                **{**base, "event_id": "ev-ui-empty"},
                content=[],
            ),
        ],
    )

    updates = [call.args[1].update for call in ctx.sender.notify.call_args_list]
    assert len(updates) == 1
    assert isinstance(updates[0], AgentMessageChunk)
    assert updates[0].content.text == "conversation answer"


@pytest.mark.anyio
async def test_replay_events_uses_conversation_text_when_only_tool_ui_exists(
    tmp_path: Path,
) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()
    base = _base(tmp_path)

    await replay._replay_events(
        ctx,
        _session(tmp_path),
        [
            UserMessageEvent(
                **{**base, "event_id": "ev-user-ui"},
                content=[{"type": "text", "text": "ui user"}],
            ),
            ConversationMessageEvent(
                **{**base, "event_id": "ev-user-conv"},
                message={
                    "role": "user",
                    "content": [{"type": "text", "text": "conversation user"}],
                },
            ),
            ToolCallEvent(
                **{**base, "event_id": "ev-tool-ui"},
                tool_call_id="tool-ui",
                title="Bash",
                kind="execute",
                raw_input="{}",
            ),
            ConversationMessageEvent(
                **{**base, "event_id": "ev-assistant-conv"},
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "conversation answer"},
                        {"type": "tool_use", "id": "tool-conv", "name": "Bash", "input": {}},
                    ],
                },
            ),
        ],
    )

    updates = [call.args[1].update for call in ctx.sender.notify.call_args_list]
    assert [type(update) for update in updates] == [
        UserMessageChunk,
        ToolCallStart,
        AgentMessageChunk,
    ]
    assert updates[0].content.text == "ui user"
    assert updates[1].tool_call_id == "tool-ui"
    assert updates[2].content.text == "conversation answer"


@pytest.mark.anyio
async def test_replay_turn_completed_emits_usage_update(tmp_path: Path) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()

    await replay._replay_event(
        ctx,
        _session(tmp_path),
        TurnCompletedEvent(
            **_base(tmp_path),
            stop_reason="stop",
            duration_ms=1234,
            input_tokens=14443,
            output_tokens=125,
        ),
    )

    update = ctx.sender.notify.call_args.args[1].update
    assert isinstance(update, UsageUpdate)
    assert update.input_tokens == 14443
    assert update.output_tokens == 125
    assert update.used == 14568
    assert update.duration_ms == 1234


@pytest.mark.anyio
async def test_replay_tool_update_restores_spilled_content_and_locations(tmp_path: Path) -> None:
    store = _Store()
    store.spilled[("s-1", "abc123")] = "full tool output"
    replay = _Replay(store)
    ctx = _ctx()

    await replay._replay_event(
        ctx,
        _session(tmp_path),
        ToolCallUpdateEvent(
            **_base(tmp_path),
            tool_call_id="tool-1",
            status="completed",
            content=[
                {
                    "type": "spilled",
                    "path": str(tmp_path / "abc123.txt"),
                    "preview": "short",
                }
            ],
            locations=[{"path": "src/kernel/kernel/app.py", "line": 4}],
        ),
    )

    notification = ctx.sender.notify.call_args.args[1]
    update = notification.update
    assert isinstance(update, ToolCallUpdateNotification)
    assert update.content == [{"type": "text", "text": "full tool output"}]
    assert update.locations[0].path == "src/kernel/kernel/app.py"
    assert update.locations[0].line == 4


@pytest.mark.anyio
async def test_replay_tool_update_uses_spill_preview_when_sidecar_missing(tmp_path: Path) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()

    await replay._replay_event(
        ctx,
        _session(tmp_path),
        ToolCallUpdateEvent(
            **_base(tmp_path),
            tool_call_id="tool-1",
            status="completed",
            content=[
                {
                    "type": "spilled",
                    "path": str(tmp_path / "missing.txt"),
                    "preview": "short preview",
                }
            ],
        ),
    )

    notification = ctx.sender.notify.call_args.args[1]
    assert notification.update.content == [{"type": "text", "text": "short preview"}]


@pytest.mark.anyio
async def test_replay_config_option_sends_descriptor_shape(tmp_path: Path) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()

    await replay._replay_event(
        ctx,
        _session(tmp_path),
        ConfigOptionChangedEvent(
            **_base(tmp_path),
            config_id="mode",
            value="plan",
            full_state={"mode": "plan"},
        ),
    )

    notification = ctx.sender.notify.call_args.args[1]
    assert isinstance(notification.update, ConfigOptionUpdate)
    assert notification.update.config_options[0]["configId"] == "mode"
    assert notification.update.config_options[0]["currentValue"] == "plan"


@pytest.mark.anyio
async def test_replay_skips_permission_bookkeeping_events(tmp_path: Path) -> None:
    replay = _Replay(_Store())
    ctx = _ctx()

    await replay._replay_event(
        ctx,
        _session(tmp_path),
        PermissionRequestEvent(
            **_base(tmp_path),
            tool_call_id="tool-1",
            tool_name="Bash",
            input_summary="rm -rf",
            risk_level="high",
        ),
    )

    ctx.sender.notify.assert_not_called()
