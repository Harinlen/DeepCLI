"""Tests for Phase 5.5.4F — Multi-layer compression (snip + micro-compact).

Covers:
- snip_tool_results: truncates old tool_result content
- micro_compact: removes entire read-only tool rounds
- _build_tool_name_index: tool_call_id → tool_name mapping
- Protected tail messages are never modified
- Cascade: snip sufficient, micro sufficient, full needed
"""

from __future__ import annotations

import pytest

from daemon.engine.compact import (
    _build_tool_name_index,
    micro_compact,
    snip_tool_results,
)
from daemon.engine.compact_types import MIN_MESSAGES_TO_KEEP
from daemon.providers.base import (
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)


# -- Helpers -----------------------------------------------------------------


def _make_tool_call(
    tool_name: str,
    call_id: str,
    args: dict | None = None,
) -> ToolUseContent:
    return ToolUseContent(
        tool_call_id=call_id,
        name=tool_name,
        arguments=args or {},
    )


def _make_tool_result(
    call_id: str,
    output: str,
    is_error: bool = False,
) -> ToolResultContent:
    return ToolResultContent(
        tool_call_id=call_id,
        output=output,
        is_error=is_error,
    )


def _build_conversation_with_greps(n_grep: int) -> list[Message]:
    """Build a conversation with n grep tool calls and large results."""
    messages: list[Message] = [
        Message.user("Search the codebase"),
    ]
    for i in range(n_grep):
        call_id = f"grep_{i}"
        messages.append(
            Message(role="assistant", content=[
                _make_tool_call("grep", call_id, {"pattern": f"search_{i}"}),
            ])
        )
        messages.append(
            Message(role="tool", content=[
                _make_tool_result(call_id, "x" * 500),  # large result
            ])
        )
    # Final assistant response
    messages.append(
        Message(role="assistant", content=[TextContent(text="Found results.")])
    )
    # Final user message
    messages.append(Message.user("Thanks"))
    return messages


def _build_mixed_conversation() -> list[Message]:
    """Build a conversation with mixed read-only and write tool calls."""
    messages = [
        Message.user("Help me edit files"),
        # Round 1: grep (read-only)
        Message(role="assistant", content=[
            _make_tool_call("grep", "c1", {"pattern": "foo"}),
        ]),
        Message(role="tool", content=[
            _make_tool_result("c1", "match found at line 10"),
        ]),
        # Round 2: file_edit (write)
        Message(role="assistant", content=[
            _make_tool_call("file_edit", "c2", {"file_path": "/a.py"}),
        ]),
        Message(role="tool", content=[
            _make_tool_result("c2", "Replaced 1 occurrence"),
        ]),
        # Round 3: file_read (read-only)
        Message(role="assistant", content=[
            _make_tool_call("file_read", "c3", {"file_path": "/b.py"}),
        ]),
        Message(role="tool", content=[
            _make_tool_result("c3", "y" * 300),
        ]),
        # Protected tail
        Message(role="assistant", content=[TextContent(text="Done editing.")]),
        Message.user("Good"),
        Message(role="assistant", content=[TextContent(text="You're welcome.")]),
        Message.user("Bye"),
    ]
    return messages


# -- _build_tool_name_index --------------------------------------------------


class TestBuildToolNameIndex:
    def test_basic_index(self) -> None:
        messages = [
            Message(role="assistant", content=[
                _make_tool_call("grep", "c1"),
                _make_tool_call("file_read", "c2"),
            ]),
        ]
        index = _build_tool_name_index(messages)
        assert index == {"c1": "grep", "c2": "file_read"}

    def test_empty_messages(self) -> None:
        assert _build_tool_name_index([]) == {}

    def test_no_tool_calls(self) -> None:
        messages = [Message.user("hello")]
        assert _build_tool_name_index(messages) == {}


# -- snip_tool_results -------------------------------------------------------


class TestSnipToolResults:
    def test_snips_large_grep_results(self) -> None:
        messages = _build_conversation_with_greps(5)
        snipped, freed = snip_tool_results(messages, MIN_MESSAGES_TO_KEEP)
        assert freed > 0
        # Original results were 500 chars each, now should be placeholders
        for msg in snipped[:-MIN_MESSAGES_TO_KEEP]:
            for block in msg.content:
                if isinstance(block, ToolResultContent):
                    assert block.output.startswith("[result truncated")

    def test_protected_tail_untouched(self) -> None:
        messages = _build_conversation_with_greps(5)
        snipped, _ = snip_tool_results(messages, MIN_MESSAGES_TO_KEEP)
        # Last MIN_MESSAGES_TO_KEEP should be identical
        for orig, new in zip(
            messages[-MIN_MESSAGES_TO_KEEP:],
            snipped[-MIN_MESSAGES_TO_KEEP:],
        ):
            assert orig is new

    def test_small_results_not_snipped(self) -> None:
        """Results <= 100 chars are not snipped."""
        messages = [
            Message.user("test"),
            Message(role="assistant", content=[
                _make_tool_call("grep", "c1"),
            ]),
            Message(role="tool", content=[
                _make_tool_result("c1", "short"),  # <100 chars
            ]),
            Message(role="assistant", content=[TextContent(text="done")]),
            Message.user("ok"),
            Message(role="assistant", content=[TextContent(text="bye")]),
            Message.user("bye"),
        ]
        _, freed = snip_tool_results(messages, MIN_MESSAGES_TO_KEEP)
        assert freed == 0

    def test_non_snippable_tools_untouched(self) -> None:
        """file_edit results are never snipped."""
        messages = [
            Message.user("edit"),
            Message(role="assistant", content=[
                _make_tool_call("file_edit", "c1"),
            ]),
            Message(role="tool", content=[
                _make_tool_result("c1", "x" * 500),
            ]),
            Message(role="assistant", content=[TextContent(text="done")]),
            Message.user("ok"),
            Message(role="assistant", content=[TextContent(text="bye")]),
            Message.user("bye"),
        ]
        _, freed = snip_tool_results(messages, MIN_MESSAGES_TO_KEEP)
        assert freed == 0

    def test_too_few_messages(self) -> None:
        messages = [Message.user("hi"), Message(role="assistant", content=[TextContent(text="hey")])]
        result, freed = snip_tool_results(messages, MIN_MESSAGES_TO_KEEP)
        assert result is messages
        assert freed == 0


# -- micro_compact -----------------------------------------------------------


class TestMicroCompact:
    def test_removes_read_only_rounds(self) -> None:
        messages = _build_mixed_conversation()
        compacted, freed = micro_compact(messages, MIN_MESSAGES_TO_KEEP)
        assert freed > 0
        # Should have fewer messages
        assert len(compacted) < len(messages)
        # The marker should be present
        marker_found = False
        for msg in compacted:
            for block in msg.content:
                if isinstance(block, TextContent) and "read-only tool messages removed" in block.text:
                    marker_found = True
        assert marker_found

    def test_write_tool_rounds_preserved(self) -> None:
        messages = _build_mixed_conversation()
        compacted, _ = micro_compact(messages, MIN_MESSAGES_TO_KEEP)
        # file_edit round should still be present
        edit_found = False
        for msg in compacted:
            for block in msg.content:
                if isinstance(block, ToolUseContent) and block.name == "file_edit":
                    edit_found = True
        assert edit_found

    def test_protected_tail_untouched(self) -> None:
        messages = _build_mixed_conversation()
        compacted, _ = micro_compact(messages, MIN_MESSAGES_TO_KEEP)
        for orig, new in zip(
            messages[-MIN_MESSAGES_TO_KEEP:],
            compacted[-MIN_MESSAGES_TO_KEEP:],
        ):
            assert orig is new

    def test_no_read_only_rounds(self) -> None:
        """All tool calls are writes — nothing to remove."""
        messages = [
            Message.user("edit"),
            Message(role="assistant", content=[
                _make_tool_call("file_edit", "c1"),
            ]),
            Message(role="tool", content=[
                _make_tool_result("c1", "Replaced"),
            ]),
            Message(role="assistant", content=[TextContent(text="done")]),
            Message.user("ok"),
            Message(role="assistant", content=[TextContent(text="bye")]),
            Message.user("bye"),
        ]
        compacted, freed = micro_compact(messages, MIN_MESSAGES_TO_KEEP)
        assert freed == 0
        assert len(compacted) == len(messages)

    def test_too_few_messages(self) -> None:
        messages = [Message.user("hi")]
        result, freed = micro_compact(messages, MIN_MESSAGES_TO_KEEP)
        assert result is messages
        assert freed == 0

    def test_parallel_read_only_tools(self) -> None:
        """Assistant with multiple read-only tools in one message."""
        messages = [
            Message.user("search"),
            Message(role="assistant", content=[
                _make_tool_call("grep", "c1"),
                _make_tool_call("glob", "c2"),
            ]),
            Message(role="tool", content=[
                _make_tool_result("c1", "match1"),
                _make_tool_result("c2", "file1.py"),
            ]),
            Message(role="assistant", content=[TextContent(text="found")]),
            Message.user("ok"),
            Message(role="assistant", content=[TextContent(text="bye")]),
            Message.user("bye"),
        ]
        compacted, freed = micro_compact(messages, MIN_MESSAGES_TO_KEEP)
        assert freed > 0
        # Tool calls should be gone, replaced by marker
        tool_calls_remaining = [
            b for m in compacted for b in m.content if isinstance(b, ToolUseContent)
        ]
        assert len(tool_calls_remaining) == 0


# -- CompactNotification strategy field --------------------------------------


class TestCompactNotificationStrategy:
    def test_default_strategy(self) -> None:
        from daemon.engine.stream import CompactNotification

        n = CompactNotification(summary_preview="test", messages_summarized=5)
        assert n.strategy == "full"
        assert n.tokens_freed == 0

    def test_snip_strategy(self) -> None:
        from daemon.engine.stream import CompactNotification

        n = CompactNotification(
            summary_preview="snipped",
            messages_summarized=0,
            strategy="snip",
            tokens_freed=1000,
        )
        assert n.strategy == "snip"
        assert n.tokens_freed == 1000
