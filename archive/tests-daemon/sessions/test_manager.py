"""Tests for Session and SessionManager."""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.config.schema import RuntimeConfig
from daemon.engine.stream import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    UsageInfo,
)
from daemon.extensions.manager import ExtensionManager
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition
from daemon.providers.registry import ProviderRegistry
from daemon.sessions.entry import (
    AssistantMessageEntry,
    ToolCallEntry,
    UserMessageEntry,
)
from daemon.sessions.manager import Session, SessionManager, _rebuild_conversation


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class FakeProvider(Provider):
    """Minimal provider for testing."""

    name = "local"

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta(content="Hello!")
        yield StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5))

    async def models(self) -> list[ModelInfo]:
        return []


def _make_registry() -> ProviderRegistry:
    """Build a provider registry with FakeProvider."""
    reg = ProviderRegistry()
    reg._default_provider = "local"
    reg.register(FakeProvider())
    return reg


def _make_config() -> RuntimeConfig:
    """Minimal RuntimeConfig."""
    from daemon.config.defaults import apply_defaults
    from daemon.config.schema import SourceConfig

    return apply_defaults(SourceConfig())


def _make_ext_manager(config: RuntimeConfig) -> ExtensionManager:
    """Create an ExtensionManager without loading anything."""
    mgr = ExtensionManager(config)
    # Don't await load_all() — leave registries empty for unit tests
    return mgr


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    """Temporary session directory."""
    return tmp_path / "sessions"


@pytest.fixture
def manager(session_dir: Path) -> SessionManager:
    """SessionManager with fake provider."""
    config = _make_config()
    return SessionManager(
        registry=_make_registry(),
        config=config,
        ext_manager=_make_ext_manager(config),
        session_dir=session_dir,
    )


# ------------------------------------------------------------------
# Session
# ------------------------------------------------------------------


class TestSession:
    """Tests for the Session class."""

    def test_add_remove_connection(self) -> None:
        """Connections can be added and removed."""
        orch = MagicMock()
        writer = MagicMock()
        session = Session("s1", orch, writer)

        ws1 = AsyncMock()
        ws2 = AsyncMock()

        session.add_connection(ws1)
        assert session.connection_count == 1

        session.add_connection(ws2)
        assert session.connection_count == 2

        session.remove_connection(ws1)
        assert session.connection_count == 1

        # Removing again is safe
        session.remove_connection(ws1)
        assert session.connection_count == 1

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self) -> None:
        """broadcast sends event to all connected clients."""
        orch = MagicMock()
        writer = MagicMock()
        session = Session("s1", orch, writer)

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        session.add_connection(ws1)
        session.add_connection(ws2)

        event = MagicMock()
        event.model_dump.return_value = {"type": "text_delta", "content": "hi"}

        await session.broadcast(event)

        ws1.send_json.assert_called_once_with({"type": "text_delta", "content": "hi"})
        ws2.send_json.assert_called_once_with({"type": "text_delta", "content": "hi"})

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self) -> None:
        """Dead connections are removed during broadcast."""
        orch = MagicMock()
        writer = MagicMock()
        session = Session("s1", orch, writer)

        ws_ok = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_json.side_effect = Exception("disconnected")

        session.add_connection(ws_ok)
        session.add_connection(ws_dead)

        event = MagicMock()
        event.model_dump.return_value = {"type": "end"}

        await session.broadcast(event)

        assert session.connection_count == 1
        assert ws_ok in session.connections

    @pytest.mark.asyncio
    async def test_send_to_unicast(self) -> None:
        """send_to sends event to specific connection only."""
        orch = MagicMock()
        writer = MagicMock()
        session = Session("s1", orch, writer)

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        session.add_connection(ws1)
        session.add_connection(ws2)

        event = MagicMock()
        event.model_dump.return_value = {"type": "permission_request"}

        await session.send_to(ws1, event)

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_not_called()

    def test_write_entry_delegates(self) -> None:
        """write_entry calls through to the TranscriptWriter."""
        orch = MagicMock()
        writer = MagicMock()
        session = Session("s1", orch, writer)

        entry = UserMessageEntry(content="test")
        session.write_entry(entry)

        writer.append.assert_called_once_with(entry)


# ------------------------------------------------------------------
# SessionManager
# ------------------------------------------------------------------


class TestSessionManager:
    """Tests for SessionManager lifecycle."""

    def test_create(self, manager: SessionManager) -> None:
        """create() produces a session with a unique ID."""
        session = manager.create()
        assert session.session_id
        assert session.orchestrator is not None
        assert session.writer is not None
        assert manager.active_count == 1

    def test_create_multiple(self, manager: SessionManager) -> None:
        """Multiple creates produce distinct sessions."""
        s1 = manager.create()
        s2 = manager.create()
        assert s1.session_id != s2.session_id
        assert manager.active_count == 2

    def test_get_existing(self, manager: SessionManager) -> None:
        """get() returns active session by ID."""
        session = manager.create()
        found = manager.get(session.session_id)
        assert found is session

    def test_get_missing(self, manager: SessionManager) -> None:
        """get() returns None for unknown ID."""
        assert manager.get("nonexistent") is None

    def test_resume_creates_from_disk(self, manager: SessionManager) -> None:
        """resume() loads a session from persisted JSONL."""
        # Create and populate a session
        s1 = manager.create()
        sid = s1.session_id
        s1.writer.append(UserMessageEntry(content="hello"))
        s1.writer.append(
            AssistantMessageEntry(
                content=[{"type": "text", "text": "world"}],
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        )

        # Remove from memory to force disk load
        manager._sessions.pop(sid)
        assert manager.get(sid) is None

        # Resume
        resumed = manager.resume(sid)
        assert resumed.session_id == sid
        assert resumed.orchestrator.conversation.message_count == 2

    def test_resume_returns_active(self, manager: SessionManager) -> None:
        """resume() reuses an already-active session."""
        session = manager.create()
        resumed = manager.resume(session.session_id)
        assert resumed is session

    def test_resume_nonexistent_raises(self, manager: SessionManager) -> None:
        """resume() raises FileNotFoundError for unknown session."""
        with pytest.raises(FileNotFoundError):
            manager.resume("does-not-exist")

    def test_resume_invalidates_git_status(self, manager: SessionManager) -> None:
        """Resumed sessions have git_status cache cleared (may be stale)."""
        s1 = manager.create()
        sid = s1.session_id
        s1.writer.append(UserMessageEntry(content="hi"))

        # Simulate a fetched git status from the original session.
        s1.orchestrator.prompt_builder._git_status = "Current branch: old"

        # Force disk reload.
        manager._sessions.pop(sid)
        resumed = manager.resume(sid)

        # After resume, the cache must be cleared so next query re-fetches.
        from daemon.engine.orchestrator.prompt_builder import SystemPromptBuilder

        assert (
            resumed.orchestrator.prompt_builder._git_status is SystemPromptBuilder.GIT_STATUS_UNSET
        )

    def test_list_sessions_empty(self, manager: SessionManager) -> None:
        """list_sessions returns empty when no sessions exist."""
        assert manager.list_sessions() == []

    def test_list_sessions(self, manager: SessionManager) -> None:
        """list_sessions returns meta for persisted sessions."""
        s1 = manager.create()
        s1.writer.append(UserMessageEntry(content="a"))
        s2 = manager.create()
        s2.writer.append(UserMessageEntry(content="b"))

        metas = manager.list_sessions()
        assert len(metas) == 2
        ids = {m.session_id for m in metas}
        assert s1.session_id in ids
        assert s2.session_id in ids

    def test_delete_active(self, manager: SessionManager) -> None:
        """delete() removes an active session from memory and disk."""
        session = manager.create()
        sid = session.session_id
        session.writer.append(UserMessageEntry(content="delete me"))
        assert session.writer.jsonl_path.exists()

        assert manager.delete(sid) is True
        assert manager.get(sid) is None
        assert not session.writer.jsonl_path.exists()

    def test_delete_persisted(self, manager: SessionManager) -> None:
        """delete() removes a persisted-only session from disk."""
        session = manager.create()
        sid = session.session_id
        session.writer.append(UserMessageEntry(content="data"))

        # Remove from memory
        manager._sessions.pop(sid)

        assert manager.delete(sid) is True

    def test_delete_nonexistent(self, manager: SessionManager) -> None:
        """delete() returns False for unknown session."""
        assert manager.delete("nope") is False

    def test_active_sessions(self, manager: SessionManager) -> None:
        """active_sessions returns current in-memory sessions."""
        manager.create()
        manager.create()
        assert len(manager.active_sessions()) == 2


# ------------------------------------------------------------------
# _rebuild_conversation
# ------------------------------------------------------------------


class TestRebuildConversation:
    """Tests for conversation reconstruction from entry chains."""

    def test_basic_roundtrip(self) -> None:
        """User + assistant entries rebuild correctly."""
        chain = [
            UserMessageEntry(content="hi"),
            AssistantMessageEntry(
                content=[{"type": "text", "text": "hello!"}],
            ),
        ]
        conv = _rebuild_conversation(chain)
        assert conv.message_count == 2
        msgs = conv.get_messages()
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"

    def test_tool_call_entries(self) -> None:
        """ToolCallEntry produces tool-result messages."""
        chain = [
            UserMessageEntry(content="run ls"),
            AssistantMessageEntry(
                content=[
                    {"type": "text", "text": "I'll run ls"},
                    {
                        "type": "tool_use",
                        "tool_call_id": "tc1",
                        "name": "bash",
                        "arguments": {"command": "ls"},
                    },
                ],
            ),
            ToolCallEntry(
                tool_call_id="tc1",
                tool_name="bash",
                arguments={"command": "ls"},
                output="file.txt",
            ),
            AssistantMessageEntry(
                content=[{"type": "text", "text": "I see file.txt"}],
            ),
        ]
        conv = _rebuild_conversation(chain)
        assert conv.message_count == 4
        # User, Assistant (text+tool_use), Tool result, Assistant
        msgs = conv.get_messages()
        assert msgs[2].role == "tool"

    def test_strips_trailing_unresolved_tool_calls(self) -> None:
        """Unresolved tool_use at the tail is stripped."""
        chain = [
            UserMessageEntry(content="do something"),
            AssistantMessageEntry(
                content=[
                    {
                        "type": "tool_use",
                        "tool_call_id": "tc1",
                        "name": "bash",
                        "arguments": {},
                    },
                ],
            ),
            # No ToolCallEntry — interrupted mid-execution
        ]
        conv = _rebuild_conversation(chain)
        # The assistant message with unresolved tool_use should be removed
        msgs = conv.get_messages()
        assert len(msgs) == 1  # Only the user message
        assert msgs[0].role == "user"

    def test_empty_chain(self) -> None:
        """Empty chain produces empty conversation."""
        conv = _rebuild_conversation([])
        assert conv.message_count == 0
