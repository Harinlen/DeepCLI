"""Tests for conversation history management."""

from __future__ import annotations

import pytest

from daemon.engine.conversation import Conversation
from daemon.providers.base import Message, TextContent, ToolUseContent


class TestConversation:
    """Tests for Conversation class."""

    def test_empty_conversation(self) -> None:
        conv = Conversation()
        assert conv.message_count == 0
        assert conv.get_messages() == []

    @pytest.mark.asyncio
    async def test_add_user_message(self) -> None:
        conv = Conversation()
        msg = await conv.add_user_message("hello")
        assert msg.role == "user"
        assert conv.message_count == 1

    @pytest.mark.asyncio
    async def test_add_assistant_text(self) -> None:
        conv = Conversation()
        msg = await conv.add_assistant_text("hi there")
        assert msg.role == "assistant"
        assert isinstance(msg.content[0], TextContent)
        assert msg.content[0].text == "hi there"

    @pytest.mark.asyncio
    async def test_add_assistant_message_mixed_content(self) -> None:
        conv = Conversation()
        content = [
            TextContent(text="Let me check"),
            ToolUseContent(tool_call_id="tc_1", name="bash", arguments={"command": "ls"}),
        ]
        msg = await conv.add_assistant_message(content)
        assert msg.role == "assistant"
        assert len(msg.content) == 2

    @pytest.mark.asyncio
    async def test_add_tool_result(self) -> None:
        conv = Conversation()
        msg = await conv.add_tool_result("tc_1", "file.txt")
        assert msg.role == "tool"
        assert conv.message_count == 1

    @pytest.mark.asyncio
    async def test_add_tool_result_error(self) -> None:
        conv = Conversation()
        msg = await conv.add_tool_result("tc_1", "not found", is_error=True)
        assert msg.content[0].is_error is True  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        conv = Conversation()
        await conv.add_user_message("hello")
        await conv.add_assistant_text("hi")
        assert conv.message_count == 2
        await conv.clear()
        assert conv.message_count == 0

    @pytest.mark.asyncio
    async def test_get_messages_returns_copy(self) -> None:
        conv = Conversation()
        await conv.add_user_message("hello")
        msgs = conv.get_messages()
        msgs.clear()
        assert conv.message_count == 1

    @pytest.mark.asyncio
    async def test_last_assistant_text(self) -> None:
        conv = Conversation()
        await conv.add_user_message("hello")
        await conv.add_assistant_text("first reply")
        await conv.add_user_message("another question")
        await conv.add_assistant_text("second reply")
        assert conv.last_assistant_text == "second reply"

    def test_last_assistant_text_none_when_empty(self) -> None:
        conv = Conversation()
        assert conv.last_assistant_text is None

    @pytest.mark.asyncio
    async def test_last_assistant_text_none_when_only_user(self) -> None:
        conv = Conversation()
        await conv.add_user_message("hello")
        assert conv.last_assistant_text is None

    @pytest.mark.asyncio
    async def test_pending_tool_calls_empty(self) -> None:
        conv = Conversation()
        await conv.add_user_message("hello")
        await conv.add_assistant_text("just text")
        assert conv.pending_tool_calls == []

    @pytest.mark.asyncio
    async def test_pending_tool_calls_unresolved(self) -> None:
        conv = Conversation()
        await conv.add_user_message("read a file")
        await conv.add_assistant_message(
            [
                ToolUseContent(
                    tool_call_id="tc_1",
                    name="file_read",
                    arguments={"path": "/tmp/test"},
                ),
            ]
        )
        pending = conv.pending_tool_calls
        assert len(pending) == 1
        assert pending[0].tool_call_id == "tc_1"

    @pytest.mark.asyncio
    async def test_pending_tool_calls_resolved(self) -> None:
        conv = Conversation()
        await conv.add_user_message("read a file")
        await conv.add_assistant_message(
            [
                ToolUseContent(
                    tool_call_id="tc_1",
                    name="file_read",
                    arguments={"path": "/tmp/test"},
                ),
            ]
        )
        await conv.add_tool_result("tc_1", "file contents")
        assert conv.pending_tool_calls == []

    @pytest.mark.asyncio
    async def test_strip_orphaned_tool_calls_noop_when_clean(self) -> None:
        """No orphans → no changes, returns 0."""
        conv = Conversation()
        await conv.add_user_message("hi")
        await conv.add_assistant_text("hello")
        assert await conv.strip_orphaned_tool_calls() == 0
        assert conv.message_count == 2

    @pytest.mark.asyncio
    async def test_strip_orphaned_tool_calls_removes_pure_tool_use(self) -> None:
        """Assistant with only tool_use → whole message removed."""
        conv = Conversation()
        await conv.add_user_message("read x")
        await conv.add_assistant_message(
            [
                ToolUseContent(
                    tool_call_id="tc_1",
                    name="file_read",
                    arguments={},
                ),
            ]
        )
        removed = await conv.strip_orphaned_tool_calls()
        assert removed == 1
        assert conv.message_count == 1  # only the user message remains
        assert conv.pending_tool_calls == []

    @pytest.mark.asyncio
    async def test_strip_orphaned_tool_calls_keeps_text(self) -> None:
        """Text + tool_use → strip tool_use, keep text-only assistant."""
        conv = Conversation()
        await conv.add_user_message("read x")
        await conv.add_assistant_message(
            [
                TextContent(text="let me check that"),
                ToolUseContent(
                    tool_call_id="tc_1",
                    name="file_read",
                    arguments={},
                ),
            ]
        )
        removed = await conv.strip_orphaned_tool_calls()
        assert removed == 1
        assert conv.message_count == 2
        msgs = conv.get_messages()
        assistant_msg = msgs[-1]
        assert assistant_msg.role == "assistant"
        assert len(assistant_msg.content) == 1
        assert isinstance(assistant_msg.content[0], TextContent)

    @pytest.mark.asyncio
    async def test_strip_orphaned_tool_calls_idempotent(self) -> None:
        """Calling strip twice is safe — second call is a no-op."""
        conv = Conversation()
        await conv.add_user_message("x")
        await conv.add_assistant_message(
            [ToolUseContent(tool_call_id="tc_1", name="bash", arguments={})]
        )
        assert await conv.strip_orphaned_tool_calls() == 1
        assert await conv.strip_orphaned_tool_calls() == 0

    @pytest.mark.asyncio
    async def test_strip_orphaned_tool_calls_after_resolved_pair(self) -> None:
        """Resolved tool calls are never considered orphans."""
        conv = Conversation()
        await conv.add_user_message("read")
        await conv.add_assistant_message(
            [ToolUseContent(tool_call_id="tc_1", name="file_read", arguments={})]
        )
        await conv.add_tool_result("tc_1", "content")
        await conv.add_assistant_text("done")
        assert await conv.strip_orphaned_tool_calls() == 0
        assert conv.message_count == 4

    @pytest.mark.asyncio
    async def test_strip_interior_orphan(self) -> None:
        """Interior orphan (mid-history tool_use w/o result) is swept."""
        conv = Conversation()
        await conv.add_user_message("M1")
        await conv.add_assistant_message(
            [
                TextContent(text="let me try"),
                ToolUseContent(tool_call_id="orphan", name="bash", arguments={}),
            ]
        )
        await conv.add_user_message("M2")
        await conv.add_assistant_message(
            [ToolUseContent(tool_call_id="good", name="grep", arguments={})]
        )
        await conv.add_tool_result("good", "hits")
        await conv.add_assistant_text("done")

        removed = await conv.strip_orphaned_tool_calls()
        assert removed == 1

        assert conv.pending_tool_calls == []
        assert conv.all_unresolved_tool_calls() == []

        msgs = conv.get_messages()
        mid_assistant = msgs[1]
        assert mid_assistant.role == "assistant"
        assert len(mid_assistant.content) == 1
        assert isinstance(mid_assistant.content[0], TextContent)

    @pytest.mark.asyncio
    async def test_strip_multiple_orphans_at_different_positions(self) -> None:
        """Two orphans at different depths are both swept."""
        conv = Conversation()
        await conv.add_user_message("a")
        await conv.add_assistant_message(
            [ToolUseContent(tool_call_id="orphan_a", name="bash", arguments={})]
        )
        await conv.add_user_message("b")
        await conv.add_assistant_message(
            [ToolUseContent(tool_call_id="good", name="grep", arguments={})]
        )
        await conv.add_tool_result("good", "ok")
        await conv.add_user_message("c")
        await conv.add_assistant_message(
            [ToolUseContent(tool_call_id="orphan_b", name="glob", arguments={})]
        )
        assert await conv.strip_orphaned_tool_calls() == 2
        assert conv.all_unresolved_tool_calls() == []

    @pytest.mark.asyncio
    async def test_all_unresolved_tool_calls_finds_interior(self) -> None:
        """all_unresolved_tool_calls sees beyond the tail."""
        conv = Conversation()
        await conv.add_user_message("m")
        await conv.add_assistant_message(
            [ToolUseContent(tool_call_id="orphan", name="bash", arguments={})]
        )
        await conv.add_user_message("next")
        await conv.add_assistant_text("hi")
        assert conv.pending_tool_calls == []
        unresolved = conv.all_unresolved_tool_calls()
        assert len(unresolved) == 1
        assert unresolved[0].tool_call_id == "orphan"

    @pytest.mark.asyncio
    async def test_replace_messages(self) -> None:
        """replace_messages() should swap the entire history."""
        conv = Conversation()
        await conv.add_user_message("old")
        await conv.add_assistant_text("old reply")
        assert conv.message_count == 2

        new_msgs = [Message.user("new"), Message.assistant_text("new reply")]
        await conv.replace_messages(new_msgs)
        assert conv.message_count == 2
        msgs = conv.get_messages()
        assert msgs[0].content[0].text == "new"  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_replace_messages_is_a_copy(self) -> None:
        """Modifying the input list after replace should not affect conversation."""
        conv = Conversation()
        original = [Message.user("a")]
        await conv.replace_messages(original)
        original.append(Message.user("b"))
        assert conv.message_count == 1

    def test_sync_append_for_rebuild(self) -> None:
        """_append() works without an event loop (for session rebuild)."""
        conv = Conversation()
        conv._append(Message.user("hello"))
        conv._append(Message.assistant_text("hi"))
        assert conv.message_count == 2

    def test_strip_orphaned_sync_for_rebuild(self) -> None:
        """strip_orphaned_tool_calls_sync works without event loop."""
        conv = Conversation()
        conv._append(Message.user("x"))
        conv._append(
            Message(
                role="assistant",
                content=[ToolUseContent(tool_call_id="tc_1", name="bash", arguments={})],
            )
        )
        assert conv.strip_orphaned_tool_calls_sync() == 1
        assert conv.message_count == 1
