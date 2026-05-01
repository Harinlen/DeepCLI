"""Tests for session entry types."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from daemon.sessions.entry import (
    AssistantMessageEntry,
    CompactBoundaryEntry,
    Entry,
    SessionMetaEntry,
    ToolCallEntry,
    UserMessageEntry,
)

_entry_adapter = TypeAdapter(Entry)


class TestEntryTypes:
    """Entry construction and serialisation."""

    def test_user_message_defaults(self) -> None:
        """UserMessageEntry gets auto-generated uuid and timestamp."""
        entry = UserMessageEntry(content="hello")
        assert entry.type == "user_message"
        assert entry.content == "hello"
        assert entry.uuid  # non-empty
        assert entry.parent_uuid is None
        assert entry.timestamp  # non-empty ISO string

    def test_assistant_message_with_usage(self) -> None:
        """AssistantMessageEntry stores content blocks and usage."""
        entry = AssistantMessageEntry(
            content=[{"type": "text", "text": "Hi!"}],
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        assert entry.type == "assistant_message"
        assert entry.content[0]["text"] == "Hi!"
        assert entry.usage["input_tokens"] == 10

    def test_tool_call_entry(self) -> None:
        """ToolCallEntry stores call params and result."""
        entry = ToolCallEntry(
            tool_call_id="tc_1",
            tool_name="bash",
            arguments={"command": "ls"},
            output="file.txt",
        )
        assert entry.type == "tool_call"
        assert entry.tool_name == "bash"
        assert entry.is_error is False

    def test_compact_boundary_entry(self) -> None:
        """CompactBoundaryEntry stores summary."""
        entry = CompactBoundaryEntry(summary="Previous conversation about X")
        assert entry.type == "compact_boundary"
        assert entry.preserved_count == 0

    def test_session_meta_entry(self) -> None:
        """SessionMetaEntry stores key/value pair."""
        entry = SessionMetaEntry(key="title", value="My Session")
        assert entry.type == "session_meta"


class TestEntrySerialization:
    """JSON round-trip for all entry types."""

    def test_user_message_roundtrip(self) -> None:
        """UserMessageEntry survives JSON serialisation."""
        original = UserMessageEntry(content="test input")
        data = json.loads(original.model_dump_json())
        restored = _entry_adapter.validate_python(data)
        assert isinstance(restored, UserMessageEntry)
        assert restored.content == "test input"
        assert restored.uuid == original.uuid

    def test_assistant_message_roundtrip(self) -> None:
        """AssistantMessageEntry with tool_use content round-trips."""
        original = AssistantMessageEntry(
            content=[
                {"type": "text", "text": "I'll run a command"},
                {
                    "type": "tool_use",
                    "tool_call_id": "tc_1",
                    "name": "bash",
                    "arguments": {"command": "ls"},
                },
            ],
            usage={"input_tokens": 100, "output_tokens": 50},
        )
        data = json.loads(original.model_dump_json())
        restored = _entry_adapter.validate_python(data)
        assert isinstance(restored, AssistantMessageEntry)
        assert len(restored.content) == 2
        assert restored.usage["output_tokens"] == 50

    def test_tool_call_roundtrip(self) -> None:
        """ToolCallEntry round-trips correctly."""
        original = ToolCallEntry(
            tool_call_id="tc_2",
            tool_name="file_read",
            arguments={"file_path": "/tmp/test.txt"},
            output="contents here",
            is_error=False,
        )
        data = json.loads(original.model_dump_json())
        restored = _entry_adapter.validate_python(data)
        assert isinstance(restored, ToolCallEntry)
        assert restored.tool_name == "file_read"

    def test_discriminated_union_dispatch(self) -> None:
        """TypeAdapter dispatches on the 'type' discriminator."""
        data = {"type": "compact_boundary", "summary": "old stuff", "preserved_count": 3}
        entry = _entry_adapter.validate_python(data)
        assert isinstance(entry, CompactBoundaryEntry)
        assert entry.preserved_count == 3

    def test_parent_uuid_chain(self) -> None:
        """Entries can form a chain via parent_uuid."""
        e1 = UserMessageEntry(content="first")
        e2 = AssistantMessageEntry(
            content=[{"type": "text", "text": "reply"}],
            parent_uuid=e1.uuid,
        )
        assert e2.parent_uuid == e1.uuid
