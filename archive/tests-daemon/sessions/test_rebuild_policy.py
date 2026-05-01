"""Tests for cancelled_tool_policy in _rebuild_conversation."""

from __future__ import annotations

from daemon.providers.base import TextContent, ToolUseContent
from daemon.sessions.entry import (
    AssistantMessageEntry,
    ToolCallEntry,
    UserMessageEntry,
)
from daemon.sessions.manager import _rebuild_conversation


def _chain_with_cancelled_turn() -> list:
    """Build a chain that contains one synthetic cancelled entry.

    Structure::

        user  "check something"
        assistant  text + tool_use(tc_cancelled)
        tool_call  SYNTHETIC tc_cancelled (cancelled in executing)
        user  "resent"
        assistant  text + tool_use(tc_good)
        tool_call  real tc_good output
    """
    return [
        UserMessageEntry(content="check something"),
        AssistantMessageEntry(
            content=[
                {"type": "text", "text": "let me check"},
                {
                    "type": "tool_use",
                    "tool_call_id": "tc_cancelled",
                    "name": "bash",
                    "arguments": {"command": "git status"},
                },
            ]
        ),
        ToolCallEntry(
            tool_call_id="tc_cancelled",
            tool_name="bash",
            arguments={"command": "git status"},
            output="<cancelled mid-execution: verify state before retry>",
            is_error=True,
            synthetic=True,
            cancel_phase="executing",
        ),
        UserMessageEntry(content="resent"),
        AssistantMessageEntry(
            content=[
                {
                    "type": "tool_use",
                    "tool_call_id": "tc_good",
                    "name": "grep",
                    "arguments": {"pattern": "x"},
                },
            ]
        ),
        ToolCallEntry(
            tool_call_id="tc_good",
            tool_name="grep",
            arguments={"pattern": "x"},
            output="hits",
            is_error=False,
        ),
    ]


class TestAcknowledgePolicy:
    """Default policy: synthetic entries pass through verbatim."""

    def test_synthetic_entry_visible_to_llm(self) -> None:
        conv = _rebuild_conversation(
            _chain_with_cancelled_turn(),
            cancelled_tool_policy="acknowledge",
        )

        msgs = conv.get_messages()
        # Collect all tool_result outputs.
        outputs: list[str] = []
        for m in msgs:
            if m.role == "tool":
                for c in m.content:
                    outputs.append(c.output)

        # Cancelled marker present.
        assert any("cancelled mid-execution" in o for o in outputs)
        # Real result also present.
        assert "hits" in outputs

    def test_tool_use_for_cancelled_turn_preserved(self) -> None:
        conv = _rebuild_conversation(
            _chain_with_cancelled_turn(),
            cancelled_tool_policy="acknowledge",
        )
        # Cancelled tool_use block is still in the assistant history.
        tool_ids = [tc.tool_call_id for tc in conv.all_unresolved_tool_calls()]
        assert tool_ids == []  # all resolved because synthetic entry matched


class TestHidePolicy:
    """Hide policy: cancelled entries + paired tool_use disappear."""

    def test_synthetic_entry_and_pair_dropped(self) -> None:
        conv = _rebuild_conversation(
            _chain_with_cancelled_turn(),
            cancelled_tool_policy="hide",
        )

        msgs = conv.get_messages()

        # No tool_result mentions "cancelled".
        for m in msgs:
            if m.role == "tool":
                for c in m.content:
                    assert "cancelled" not in c.output.lower()

        # No tool_use with tc_cancelled anywhere in assistant history.
        for m in msgs:
            if m.role == "assistant":
                for c in m.content:
                    if isinstance(c, ToolUseContent):
                        assert c.tool_call_id != "tc_cancelled"

    def test_real_turn_unaffected(self) -> None:
        conv = _rebuild_conversation(
            _chain_with_cancelled_turn(),
            cancelled_tool_policy="hide",
        )
        # tc_good should still be round-tripped.
        msgs = conv.get_messages()
        found_good = False
        for m in msgs:
            for c in m.content:
                if isinstance(c, ToolUseContent) and c.tool_call_id == "tc_good":
                    found_good = True
        assert found_good

    def test_assistant_text_kept_when_tool_use_hidden(self) -> None:
        """Assistant message with text + hidden tool_use keeps the text."""
        conv = _rebuild_conversation(
            _chain_with_cancelled_turn(),
            cancelled_tool_policy="hide",
        )
        msgs = conv.get_messages()
        first_assistant = next(m for m in msgs if m.role == "assistant")
        assert any(
            isinstance(c, TextContent) and "let me check" in c.text for c in first_assistant.content
        )


class TestVerbatimPolicy:
    """Verbatim policy: prepend phase + timestamp context."""

    def test_synthetic_entry_expanded(self) -> None:
        conv = _rebuild_conversation(
            _chain_with_cancelled_turn(),
            cancelled_tool_policy="verbatim",
        )
        msgs = conv.get_messages()
        outputs: list[str] = []
        for m in msgs:
            if m.role == "tool":
                for c in m.content:
                    outputs.append(c.output)

        cancelled_output = next(o for o in outputs if "phase=executing" in o)
        # Still contains original message text.
        assert "cancelled mid-execution" in cancelled_output

    def test_real_entries_unchanged(self) -> None:
        conv = _rebuild_conversation(
            _chain_with_cancelled_turn(),
            cancelled_tool_policy="verbatim",
        )
        msgs = conv.get_messages()
        outputs: list[str] = []
        for m in msgs:
            if m.role == "tool":
                for c in m.content:
                    outputs.append(c.output)
        # Real result is still plain (no phase prefix).
        assert "hits" in outputs
