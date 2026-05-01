"""Tests for ConversationHistory."""

from __future__ import annotations


from kernel.llm.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
    UserMessage,
)
from kernel.orchestrator.history import ConversationHistory


# ---------------------------------------------------------------------------
# append_user
# ---------------------------------------------------------------------------


def test_append_user_adds_user_message() -> None:
    h = ConversationHistory()
    h.append_user([TextContent(text="hello")])

    assert len(h.messages) == 1
    assert isinstance(h.messages[0], UserMessage)
    assert h.messages[0].content[0].text == "hello"


def test_append_user_increments_token_count() -> None:
    h = ConversationHistory()
    before = h.token_count
    h.append_user([TextContent(text="hello world")])
    assert h.token_count > before


# ---------------------------------------------------------------------------
# append_assistant
# ---------------------------------------------------------------------------


def test_append_assistant_text_only() -> None:
    h = ConversationHistory()
    h.append_assistant(text="I can help.", thoughts=[], tool_calls=[])

    assert len(h.messages) == 1
    msg = h.messages[0]
    assert isinstance(msg, AssistantMessage)
    assert any(isinstance(b, TextContent) and b.text == "I can help." for b in msg.content)


def test_append_assistant_skipped_when_empty() -> None:
    h = ConversationHistory()
    h.append_assistant(text="", thoughts=[], tool_calls=[])
    assert len(h.messages) == 0


def test_append_assistant_with_tool_calls() -> None:
    h = ConversationHistory()
    tc = ToolUseContent(id="tc_1", name="bash", input={"command": "ls"})
    h.append_assistant(text="", thoughts=[], tool_calls=[tc])

    msg = h.messages[0]
    assert isinstance(msg, AssistantMessage)
    tool_blocks = [b for b in msg.content if isinstance(b, ToolUseContent)]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].name == "bash"


# ---------------------------------------------------------------------------
# ThinkingContent assembly from ThoughtChunk
# ---------------------------------------------------------------------------


def test_append_assistant_assembles_thinking_content() -> None:
    from kernel.llm.types import ThoughtChunk

    h = ConversationHistory()
    thoughts = [
        ThoughtChunk(content="Let me think", signature=""),
        ThoughtChunk(content=" about this", signature=""),
        ThoughtChunk(content="", signature="sig_abc123"),
    ]
    h.append_assistant(text="Done.", thoughts=thoughts, tool_calls=[])

    msg = h.messages[0]
    assert isinstance(msg, AssistantMessage)
    thinking_blocks = [b for b in msg.content if isinstance(b, ThinkingContent)]
    assert len(thinking_blocks) == 1
    tc = thinking_blocks[0]
    assert tc.thinking == "Let me think about this"
    assert tc.signature == "sig_abc123"


def test_append_assistant_thinking_comes_before_text() -> None:
    """Anthropic API requires thinking before text in the content list."""
    from kernel.llm.types import ThoughtChunk

    h = ConversationHistory()
    thoughts = [ThoughtChunk(content="thought", signature="sig")]
    h.append_assistant(text="answer", thoughts=thoughts, tool_calls=[])

    msg = h.messages[0]
    assert isinstance(msg.content[0], ThinkingContent)
    assert isinstance(msg.content[1], TextContent)


def test_append_assistant_no_thinking_when_thoughts_empty() -> None:
    h = ConversationHistory()
    h.append_assistant(text="answer", thoughts=[], tool_calls=[])

    msg = h.messages[0]
    thinking_blocks = [b for b in msg.content if isinstance(b, ThinkingContent)]
    assert len(thinking_blocks) == 0


# ---------------------------------------------------------------------------
# append_tool_results
# ---------------------------------------------------------------------------


def test_append_tool_results_adds_user_message() -> None:
    h = ConversationHistory()
    result = ToolResultContent(tool_use_id="tc_1", content="file contents", is_error=False)
    h.append_tool_results([result])

    assert len(h.messages) == 1
    assert isinstance(h.messages[0], UserMessage)


def test_append_tool_results_noop_when_empty() -> None:
    h = ConversationHistory()
    h.append_tool_results([])
    assert len(h.messages) == 0


# ---------------------------------------------------------------------------
# update_token_count
# ---------------------------------------------------------------------------


def test_update_token_count_replaces_estimate() -> None:
    h = ConversationHistory()
    h.append_user([TextContent(text="x" * 400)])  # ~100 tokens by estimate
    h.update_token_count(input_tokens=5000, output_tokens=200)
    assert h.token_count == 5200


def test_update_token_count_ignores_zero() -> None:
    h = ConversationHistory()
    h.append_user([TextContent(text="x" * 400)])
    before = h.token_count
    h.update_token_count(input_tokens=0, output_tokens=0)
    # token_count should not change to 0 when provider sends zeros
    assert h.token_count == before


# ---------------------------------------------------------------------------
# find_compaction_boundary
# ---------------------------------------------------------------------------


def _build_history_with_turns(n: int) -> ConversationHistory:
    """Build a history with n complete user/assistant turn pairs."""
    h = ConversationHistory()
    for i in range(n):
        h.append_user([TextContent(text=f"user turn {i}")])
        h.append_assistant(text=f"assistant turn {i}", thoughts=[], tool_calls=[])
    return h


def test_boundary_returns_zero_when_not_enough_history() -> None:
    h = _build_history_with_turns(3)
    boundary = h.find_compaction_boundary(keep_recent_turns=5)
    assert boundary == 0


def test_boundary_keeps_recent_turns() -> None:
    h = _build_history_with_turns(10)
    boundary = h.find_compaction_boundary(keep_recent_turns=5)
    # boundary should be the index of the 6th user message (0-indexed turn 5)
    assert boundary > 0
    # The message at boundary must be a UserMessage
    assert isinstance(h.messages[boundary], UserMessage)


# ---------------------------------------------------------------------------
# replace_with_compacted
# ---------------------------------------------------------------------------


def test_replace_with_compacted_shrinks_history() -> None:
    h = _build_history_with_turns(10)
    original_len = len(h.messages)
    boundary = h.find_compaction_boundary(keep_recent_turns=5)

    h.replace_with_compacted(summary="Earlier conversation summary.", boundary=boundary)

    # Summary message + messages from boundary onward
    expected_len = 1 + (original_len - boundary)
    assert len(h.messages) == expected_len


def test_replace_with_compacted_first_message_is_summary() -> None:
    h = _build_history_with_turns(10)
    boundary = h.find_compaction_boundary(keep_recent_turns=5)
    h.replace_with_compacted(summary="THE SUMMARY", boundary=boundary)

    first = h.messages[0]
    assert isinstance(first, UserMessage)
    assert "THE SUMMARY" in first.content[0].text  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# pending_tool_use_ids
# ---------------------------------------------------------------------------


def test_pending_tool_use_ids_empty_when_no_messages() -> None:
    h = ConversationHistory()
    assert h.pending_tool_use_ids() == []


def test_pending_tool_use_ids_empty_for_text_only_assistant() -> None:
    h = ConversationHistory()
    h.append_user([TextContent(text="hi")])
    h.append_assistant(text="hello", thoughts=[], tool_calls=[])
    assert h.pending_tool_use_ids() == []


def test_pending_tool_use_ids_returns_orphans() -> None:
    """tool_use blocks without matching tool_results are returned."""
    h = ConversationHistory()
    h.append_user([TextContent(text="run something")])
    tc1 = ToolUseContent(id="tc_1", name="bash", input={"command": "ls"})
    tc2 = ToolUseContent(id="tc_2", name="read", input={"path": "/tmp"})
    h.append_assistant(text="", thoughts=[], tool_calls=[tc1, tc2])

    assert h.pending_tool_use_ids() == ["tc_1", "tc_2"]


def test_pending_tool_use_ids_empty_after_results_appended() -> None:
    """Once tool_results are appended, no orphans remain."""
    h = ConversationHistory()
    h.append_user([TextContent(text="q")])
    tc = ToolUseContent(id="tc_1", name="bash", input={})
    h.append_assistant(text="", thoughts=[], tool_calls=[tc])
    h.append_tool_results([ToolResultContent(tool_use_id="tc_1", content="ok", is_error=False)])

    assert h.pending_tool_use_ids() == []


def test_pending_tool_use_ids_partial_results() -> None:
    """Only tool_use IDs without matching results are returned."""
    h = ConversationHistory()
    h.append_user([TextContent(text="q")])
    tc1 = ToolUseContent(id="tc_1", name="bash", input={})
    tc2 = ToolUseContent(id="tc_2", name="read", input={})
    h.append_assistant(text="", thoughts=[], tool_calls=[tc1, tc2])
    h.append_tool_results([ToolResultContent(tool_use_id="tc_1", content="ok", is_error=False)])

    assert h.pending_tool_use_ids() == ["tc_2"]
