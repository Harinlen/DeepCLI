"""Tests for Compactor compression layers 1b (snip) and 1c (microcompact).

Uses ConversationHistory directly with hand-crafted messages — no LLM
provider needed since these layers are pure in-memory transforms.
"""

from __future__ import annotations

from typing import Any

from kernel.llm.types import (
    AssistantMessage,
    TextContent,
    ToolResultContent,
    ToolUseContent,
    UserMessage,
)
from kernel.orchestrator.compactor import Compactor
from kernel.orchestrator.history import ConversationHistory
from kernel.orchestrator.types import ToolKind


def _make_history_with_tool_pairs(
    n_read_only: int = 3,
    n_mutating: int = 1,
    tail_turns: int = 2,
) -> ConversationHistory:
    """Build a history with read-only and mutating tool pairs + tail user turns.

    Layout:
      - n_read_only assistant+tool_result pairs (read-only tools)
      - n_mutating assistant+tool_result pairs (mutating tools)
      - tail_turns user+assistant text pairs (protected tail)
    """
    history = ConversationHistory()

    # Read-only tool pairs
    for i in range(n_read_only):
        tid = f"ro-{i}"
        history._messages.append(
            AssistantMessage(content=[ToolUseContent(id=tid, name="grep", input={"q": f"search-{i}"})])
        )
        history._messages.append(
            UserMessage(content=[ToolResultContent(tool_use_id=tid, content=f"result-{i} " * 500, is_error=False)])
        )
        history.record_tool_kind(tid, ToolKind.search)

    # Mutating tool pairs
    for i in range(n_mutating):
        tid = f"mut-{i}"
        history._messages.append(
            AssistantMessage(content=[ToolUseContent(id=tid, name="bash", input={"cmd": f"run-{i}"})])
        )
        history._messages.append(
            UserMessage(content=[ToolResultContent(tool_use_id=tid, content=f"output-{i} " * 500, is_error=False)])
        )
        history.record_tool_kind(tid, ToolKind.execute)

    # Tail turns (protected)
    for i in range(tail_turns):
        history._messages.append(UserMessage(content=[TextContent(text=f"user-{i}")]))
        history._messages.append(
            AssistantMessage(content=[TextContent(text=f"assistant-{i}")])
        )

    history._token_count = history._estimate_tokens_for(history._messages)
    return history


# ---------------------------------------------------------------------------
# Layer 1b: Snip
# ---------------------------------------------------------------------------


class TestSnip:
    def test_snip_replaces_read_only_results(self) -> None:
        history = _make_history_with_tool_pairs(n_read_only=3, n_mutating=0, tail_turns=2)
        compactor = Compactor(deps=object(), model="m", keep_recent_turns=2)

        freed = compactor.snip(history)

        assert freed > 0
        # All 3 read-only tool results should be snipped.
        snipped = [
            b
            for m in history.messages
            if isinstance(m, UserMessage)
            for b in m.content
            if isinstance(b, ToolResultContent) and "snipped" in str(b.content)
        ]
        assert len(snipped) == 3
        for s in snipped:
            assert "[result snipped" in str(s.content)

    def test_snip_preserves_tail(self) -> None:
        """Tool results in the protected tail must not be snipped."""
        history = ConversationHistory()

        # One read-only pair in the tail
        tid = "ro-tail"
        history._messages.append(UserMessage(content=[TextContent(text="q1")]))
        history._messages.append(
            AssistantMessage(content=[ToolUseContent(id=tid, name="grep", input={})])
        )
        history._messages.append(
            UserMessage(content=[ToolResultContent(tool_use_id=tid, content="big result " * 100, is_error=False)])
        )
        history.record_tool_kind(tid, ToolKind.search)
        history._token_count = history._estimate_tokens_for(history._messages)

        compactor = Compactor(deps=object(), model="m", keep_recent_turns=5)
        freed = compactor.snip(history)

        # Boundary is 0 (not enough turns), so nothing should be snipped.
        assert freed == 0

    def test_snip_preserves_mutating_results(self) -> None:
        history = _make_history_with_tool_pairs(n_read_only=2, n_mutating=2, tail_turns=2)
        compactor = Compactor(deps=object(), model="m", keep_recent_turns=2)

        compactor.snip(history)

        # Mutating tool results must still have original content.
        for msg in history.messages:
            if not isinstance(msg, UserMessage):
                continue
            for block in msg.content:
                if isinstance(block, ToolResultContent) and block.tool_use_id.startswith("mut-"):
                    assert "snipped" not in str(block.content)

    def test_snip_preserves_error_results(self) -> None:
        """Error results should not be snipped (they contain diagnostic info)."""
        history = ConversationHistory()
        for i in range(6):
            history._messages.append(UserMessage(content=[TextContent(text=f"q{i}")]))
            history._messages.append(AssistantMessage(content=[TextContent(text=f"a{i}")]))

        tid = "ro-err"
        history._messages.insert(0, AssistantMessage(
            content=[ToolUseContent(id=tid, name="grep", input={})]
        ))
        history._messages.insert(1, UserMessage(
            content=[ToolResultContent(tool_use_id=tid, content="error details", is_error=True)]
        ))
        history.record_tool_kind(tid, ToolKind.search)
        history._token_count = history._estimate_tokens_for(history._messages)

        compactor = Compactor(deps=object(), model="m", keep_recent_turns=3)
        compactor.snip(history)

        # Error result should be preserved.
        err_block = history.messages[1].content[0]
        assert isinstance(err_block, ToolResultContent)
        assert err_block.content == "error details"

    def test_snip_noop_when_no_read_only(self) -> None:
        history = _make_history_with_tool_pairs(n_read_only=0, n_mutating=3, tail_turns=3)
        compactor = Compactor(deps=object(), model="m", keep_recent_turns=3)
        freed = compactor.snip(history)
        assert freed == 0


# ---------------------------------------------------------------------------
# Layer 1c: Microcompact
# ---------------------------------------------------------------------------


class TestMicrocompact:
    def test_microcompact_removes_read_only_pairs(self) -> None:
        history = _make_history_with_tool_pairs(n_read_only=3, n_mutating=0, tail_turns=3)
        original_len = len(history.messages)
        compactor = Compactor(deps=object(), model="m", keep_recent_turns=3)

        removed = compactor.microcompact(history)

        assert removed == 3
        # 6 messages removed, 1 marker added → net -5
        assert len(history.messages) == original_len - 5
        # Marker should exist.
        marker_found = any(
            isinstance(m, UserMessage)
            and any("read-only tool calls removed" in getattr(b, "text", "") for b in m.content)
            for m in history.messages
        )
        assert marker_found

    def test_microcompact_preserves_mutating_pairs(self) -> None:
        history = _make_history_with_tool_pairs(n_read_only=2, n_mutating=2, tail_turns=3)
        compactor = Compactor(deps=object(), model="m", keep_recent_turns=3)

        removed = compactor.microcompact(history)

        assert removed == 2  # only the 2 read-only pairs
        # Mutating pairs should still exist.
        mut_ids = {
            b.tool_use_id
            for m in history.messages
            if isinstance(m, UserMessage)
            for b in m.content
            if isinstance(b, ToolResultContent) and b.tool_use_id.startswith("mut-")
        }
        assert mut_ids == {"mut-0", "mut-1"}

    def test_microcompact_preserves_assistant_with_text(self) -> None:
        """Assistant messages that contain text + tool_use should not be removed."""
        history = ConversationHistory()

        # Assistant with text + tool_use (not pure tool-only).
        tid = "mixed-0"
        history._messages.append(
            AssistantMessage(content=[
                TextContent(text="Let me search for that."),
                ToolUseContent(id=tid, name="grep", input={}),
            ])
        )
        history._messages.append(
            UserMessage(content=[ToolResultContent(tool_use_id=tid, content="found it", is_error=False)])
        )
        history.record_tool_kind(tid, ToolKind.search)

        # Tail
        for i in range(4):
            history._messages.append(UserMessage(content=[TextContent(text=f"q{i}")]))
            history._messages.append(AssistantMessage(content=[TextContent(text=f"a{i}")]))

        history._token_count = history._estimate_tokens_for(history._messages)
        compactor = Compactor(deps=object(), model="m", keep_recent_turns=3)

        removed = compactor.microcompact(history)

        # Should not remove the mixed assistant message.
        assert removed == 0

    def test_microcompact_noop_when_boundary_zero(self) -> None:
        """Not enough history to compact → returns 0."""
        history = ConversationHistory()
        history._messages.append(UserMessage(content=[TextContent(text="hi")]))
        history._messages.append(AssistantMessage(content=[TextContent(text="hello")]))
        history._token_count = history._estimate_tokens_for(history._messages)

        compactor = Compactor(deps=object(), model="m", keep_recent_turns=5)
        removed = compactor.microcompact(history)
        assert removed == 0


# ---------------------------------------------------------------------------
# Layer 1a: Tool-result budget (ToolExecutor helper)
# ---------------------------------------------------------------------------


class TestToolResultBudget:
    def test_truncates_oversized_string_result(self) -> None:
        from kernel.orchestrator.tool_executor import _apply_result_budget

        big = "x" * 200
        result = _apply_result_budget(big, budget=100)
        assert isinstance(result, str)
        assert result.startswith("x" * 100)
        assert "[tool result truncated" in result
        assert "200 chars" in result

    def test_passes_through_under_budget(self) -> None:
        from kernel.orchestrator.tool_executor import _apply_result_budget

        small = "hello"
        result = _apply_result_budget(small, budget=100)
        assert result == "hello"

    def test_passes_through_list_content(self) -> None:
        from kernel.orchestrator.tool_executor import _apply_result_budget

        blocks = [{"type": "text", "text": "x" * 200}]
        result = _apply_result_budget(blocks, budget=10)
        assert result is blocks  # unchanged reference


# ---------------------------------------------------------------------------
# Compact role resolution — prefers compact, falls back to default
# ---------------------------------------------------------------------------


class _FakeProviderWithCompact:
    """Stand-in for LLMManager that has ``model_for_or_default``."""

    def __init__(self, compact_model: str | None, default_model: str) -> None:
        self._compact = compact_model
        self._default = default_model

    def model_for_or_default(self, role: str) -> str:
        if role == "compact" and self._compact is not None:
            return self._compact
        return self._default


class _FakeDeps:
    def __init__(self, provider: Any) -> None:  # noqa: ANN401
        self.provider = provider


class TestCompactModelResolution:
    def test_prefers_compact_role_when_configured(self) -> None:
        from typing import Any  # noqa: F401 (local ref for type-check)

        deps = _FakeDeps(_FakeProviderWithCompact("haiku-cheap", "sonnet-main"))
        compactor = Compactor(deps=deps, model="passed-in-fallback")
        assert compactor._model == "haiku-cheap"

    def test_falls_back_to_default_when_compact_unset(self) -> None:
        deps = _FakeDeps(_FakeProviderWithCompact(None, "sonnet-main"))
        compactor = Compactor(deps=deps, model="passed-in-fallback")
        # model_for_or_default returns the default — that's what Compactor stores.
        assert compactor._model == "sonnet-main"

    def test_falls_back_to_passed_model_when_provider_lacks_method(self) -> None:
        deps = _FakeDeps(object())  # no model_for_or_default
        compactor = Compactor(deps=deps, model="passed-in-fallback")
        assert compactor._model == "passed-in-fallback"

    def test_handles_missing_provider_attr(self) -> None:
        compactor = Compactor(deps=object(), model="explicit")
        assert compactor._model == "explicit"
