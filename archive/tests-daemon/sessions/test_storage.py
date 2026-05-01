"""Tests for TranscriptWriter and SessionMeta."""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.sessions.entry import (
    AssistantMessageEntry,
    ToolCallEntry,
    UserMessageEntry,
)
from daemon.sessions.storage import SessionMeta, TranscriptWriter


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    """Fresh temporary session directory."""
    return tmp_path / "sessions"


@pytest.fixture
def writer(session_dir: Path) -> TranscriptWriter:
    """TranscriptWriter for a test session."""
    return TranscriptWriter(session_dir, "test-session-001")


class TestSessionMeta:
    """SessionMeta model basics."""

    def test_defaults(self) -> None:
        """Default values are populated."""
        meta = SessionMeta(session_id="s1")
        assert meta.session_id == "s1"
        assert meta.title is None
        assert meta.total_input_tokens == 0
        assert meta.message_count == 0

    def test_json_roundtrip(self) -> None:
        """Meta survives JSON serialisation."""
        meta = SessionMeta(session_id="s2", title="My Chat", cwd="/tmp")
        data = meta.model_dump_json()
        restored = SessionMeta.model_validate_json(data)
        assert restored.title == "My Chat"
        assert restored.cwd == "/tmp"


class TestTranscriptWriter:
    """TranscriptWriter append/read operations."""

    def test_creates_directory(self, session_dir: Path) -> None:
        """Writer creates the session directory if it doesn't exist."""
        assert not session_dir.exists()
        TranscriptWriter(session_dir, "s1")
        assert session_dir.exists()

    def test_append_creates_jsonl(self, writer: TranscriptWriter) -> None:
        """First append creates the JSONL file."""
        assert not writer.jsonl_path.exists()
        writer.append(UserMessageEntry(content="hi"))
        assert writer.jsonl_path.exists()

    def test_append_chains_parent_uuid(self, writer: TranscriptWriter) -> None:
        """Entries are chained via parent_uuid."""
        e1 = UserMessageEntry(content="first")
        e2 = AssistantMessageEntry(content=[{"type": "text", "text": "reply"}])

        writer.append(e1)
        writer.append(e2)

        assert e1.parent_uuid is None  # First entry has no parent
        assert e2.parent_uuid == e1.uuid

    def test_append_updates_meta(self, writer: TranscriptWriter) -> None:
        """Each append increments message_count and writes meta file."""
        writer.append(UserMessageEntry(content="a"))
        writer.append(UserMessageEntry(content="b"))

        assert writer.meta.message_count == 2
        # Meta file should exist on disk
        meta_path = writer._meta_path
        assert meta_path.exists()

        # Verify persisted meta matches
        on_disk = SessionMeta.model_validate_json(meta_path.read_text())
        assert on_disk.message_count == 2

    def test_update_usage(self, writer: TranscriptWriter) -> None:
        """update_usage accumulates token counts (single model)."""
        writer.update_usage("m1", 100, 50)
        writer.update_usage("m1", 200, 100)
        assert writer.meta.total_input_tokens == 300
        assert writer.meta.total_output_tokens == 150
        assert writer.meta.model_usage["m1"].input_tokens == 300
        assert writer.meta.model_usage["m1"].output_tokens == 150

    def test_update_usage_per_model_breakdown(self, writer: TranscriptWriter) -> None:
        """Different models accumulate independently in model_usage."""
        writer.update_usage("alpha", 100, 50)
        writer.update_usage("beta", 300, 200)
        writer.update_usage("alpha", 50, 25)

        assert writer.meta.total_input_tokens == 450
        assert writer.meta.total_output_tokens == 275
        assert writer.meta.model_usage["alpha"].input_tokens == 150
        assert writer.meta.model_usage["alpha"].output_tokens == 75
        assert writer.meta.model_usage["beta"].input_tokens == 300
        assert writer.meta.model_usage["beta"].output_tokens == 200

    def test_model_usage_persisted(self, writer: TranscriptWriter) -> None:
        """Per-model breakdown survives reload."""
        writer.update_usage("alpha", 10, 5)
        writer.update_usage("beta", 20, 10)

        reloaded = TranscriptWriter(writer._dir, writer._session_id)
        assert reloaded.meta.model_usage["alpha"].input_tokens == 10
        assert reloaded.meta.model_usage["beta"].output_tokens == 10

    def test_read_all_empty(self, writer: TranscriptWriter) -> None:
        """read_all on non-existent file returns empty list."""
        assert writer.read_all() == []

    def test_read_all(self, writer: TranscriptWriter) -> None:
        """read_all returns all entries in order."""
        writer.append(UserMessageEntry(content="hello"))
        writer.append(AssistantMessageEntry(content=[{"type": "text", "text": "world"}]))
        writer.append(
            ToolCallEntry(
                tool_call_id="tc1",
                tool_name="bash",
                arguments={"command": "ls"},
                output="file.txt",
            )
        )

        entries = writer.read_all()
        assert len(entries) == 3
        assert isinstance(entries[0], UserMessageEntry)
        assert isinstance(entries[1], AssistantMessageEntry)
        assert isinstance(entries[2], ToolCallEntry)

    def test_read_all_skips_malformed_lines(self, writer: TranscriptWriter) -> None:
        """Malformed JSONL lines are skipped with a warning."""
        writer.append(UserMessageEntry(content="ok"))

        # Inject a bad line
        with open(writer.jsonl_path, "a") as f:
            f.write("not valid json\n")

        writer.append(UserMessageEntry(content="also ok"))

        entries = writer.read_all()
        assert len(entries) == 2  # Bad line skipped

    def test_read_all_rejects_oversized_file(self, writer: TranscriptWriter) -> None:
        """read_all raises ValueError for files over the size limit."""
        # Create a file larger than MAX_TRANSCRIPT_BYTES
        writer.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(writer.jsonl_path, "w") as f:
            # Write ~1 byte more than limit
            f.write("x" * (50 * 1024 * 1024 + 1))

        with pytest.raises(ValueError, match="too large"):
            writer.read_all()

    def test_read_chain(self, writer: TranscriptWriter) -> None:
        """read_chain rebuilds the linked list in order."""
        writer.append(UserMessageEntry(content="one"))
        writer.append(AssistantMessageEntry(content=[{"type": "text", "text": "two"}]))
        writer.append(UserMessageEntry(content="three"))

        chain = writer.read_chain()
        assert len(chain) == 3
        assert isinstance(chain[0], UserMessageEntry)
        assert chain[0].content == "one"
        assert chain[2].content == "three"

    def test_read_chain_empty(self, writer: TranscriptWriter) -> None:
        """read_chain on empty transcript returns empty list."""
        assert writer.read_chain() == []

    def test_read_tail(self, writer: TranscriptWriter) -> None:
        """read_tail returns entries from the end of the file."""
        for i in range(10):
            writer.append(UserMessageEntry(content=f"msg-{i}"))

        # Read last ~256 bytes — should get the last few entries
        tail = writer.read_tail(n_bytes=256)
        assert len(tail) > 0
        assert all(isinstance(e, UserMessageEntry) for e in tail)
        # Last entry should be the most recent
        assert tail[-1].content == "msg-9"

    def test_read_tail_empty(self, writer: TranscriptWriter) -> None:
        """read_tail on non-existent file returns empty list."""
        assert writer.read_tail() == []

    def test_delete(self, writer: TranscriptWriter) -> None:
        """delete removes both JSONL and meta files."""
        writer.append(UserMessageEntry(content="doomed"))
        assert writer.jsonl_path.exists()
        assert writer._meta_path.exists()

        writer.delete()
        assert not writer.jsonl_path.exists()
        assert not writer._meta_path.exists()

    def test_delete_nonexistent_is_safe(self, writer: TranscriptWriter) -> None:
        """delete on non-existent files does not raise."""
        writer.delete()  # No crash

    def test_last_uuid_tracks(self, writer: TranscriptWriter) -> None:
        """last_uuid reflects the most recently written entry."""
        assert writer.last_uuid is None

        e1 = UserMessageEntry(content="a")
        writer.append(e1)
        assert writer.last_uuid == e1.uuid

        e2 = UserMessageEntry(content="b")
        writer.append(e2)
        assert writer.last_uuid == e2.uuid

    def test_reload_from_disk(self, session_dir: Path) -> None:
        """A new TranscriptWriter loads existing meta from disk."""
        w1 = TranscriptWriter(session_dir, "persist-test")
        w1.append(UserMessageEntry(content="persisted"))
        w1.update_usage("any", 42, 7)

        # Create a new writer for the same session
        w2 = TranscriptWriter(session_dir, "persist-test")
        assert w2.meta.message_count == 1
        assert w2.meta.total_input_tokens == 42

    def test_title_auto_generated_from_first_user_message(self, writer: TranscriptWriter) -> None:
        """Title is auto-set from the first UserMessageEntry."""
        assert writer.meta.title is None
        writer.append(UserMessageEntry(content="Help me debug this crash"))
        assert writer.meta.title == "Help me debug this crash"

    def test_title_not_overwritten_by_subsequent_messages(self, writer: TranscriptWriter) -> None:
        """Once set, title is not overwritten by later user messages."""
        writer.append(UserMessageEntry(content="First message"))
        writer.append(UserMessageEntry(content="Second message"))
        assert writer.meta.title == "First message"

    def test_title_truncated_for_long_messages(self, writer: TranscriptWriter) -> None:
        """Long messages are truncated to 80 chars for the title."""
        long_msg = "x" * 200
        writer.append(UserMessageEntry(content=long_msg))
        assert writer.meta.title is not None
        assert len(writer.meta.title) == 80

    def test_title_uses_first_line_only(self, writer: TranscriptWriter) -> None:
        """Multi-line messages use only the first line as title."""
        writer.append(UserMessageEntry(content="First line\nSecond line\nThird"))
        assert writer.meta.title == "First line"

    def test_title_not_set_from_assistant_message(self, writer: TranscriptWriter) -> None:
        """AssistantMessageEntry does not trigger title auto-gen."""
        writer.append(AssistantMessageEntry(content=[{"type": "text", "text": "hello"}]))
        assert writer.meta.title is None
