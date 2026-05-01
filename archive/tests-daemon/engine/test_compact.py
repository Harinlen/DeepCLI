"""Tests for context compaction — token estimation, threshold, and LLM summarization."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from daemon.engine.compact import (
    AUTOCOMPACT_BUFFER_TOKENS,
    DEFAULT_CONTEXT_WINDOW,
    MAX_CONSECUTIVE_FAILURES,
    MIN_MESSAGES_TO_KEEP,
    CompactError,
    CompactState,
    build_post_compact_messages,
    compact,
    estimate_tokens,
    resolve_context_window,
    should_auto_compact,
)
from daemon.engine.stream import StreamEnd, StreamEvent, TextDelta, UsageInfo
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class FakeSummarizationProvider(Provider):
    """Provider that returns a fixed summary for compaction tests."""

    name = "fake_summary"

    def __init__(self, summary: str = "## 1. User Intent\nTest summary") -> None:
        self._summary = summary

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta(content=self._summary)
        yield StreamEnd(usage=UsageInfo(input_tokens=100, output_tokens=50))

    async def models(self) -> list[ModelInfo]:
        return []


class EmptySummaryProvider(Provider):
    """Provider that returns empty text — simulates a failed summarization."""

    name = "empty"

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        yield StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=0))

    async def models(self) -> list[ModelInfo]:
        return []


def _make_messages(n: int) -> list[Message]:
    """Create n alternating user/assistant messages."""
    msgs: list[Message] = []
    for i in range(n):
        if i % 2 == 0:
            msgs.append(Message.user(f"User message {i}"))
        else:
            msgs.append(Message.assistant_text(f"Assistant response {i}"))
    return msgs


# ------------------------------------------------------------------
# Token estimation
# ------------------------------------------------------------------


class TestEstimateTokens:
    """Tests for estimate_tokens()."""

    def test_empty_messages(self) -> None:
        assert estimate_tokens([]) == 0

    def test_single_short_message(self) -> None:
        msgs = [Message.user("hello")]
        tokens = estimate_tokens(msgs)
        # "hello" = 5 chars → 5 / 3 = 1
        assert tokens >= 1

    def test_longer_text(self) -> None:
        # 300 chars → ~100 tokens
        text = "x" * 300
        msgs = [Message.user(text)]
        tokens = estimate_tokens(msgs)
        assert tokens == 100

    def test_tool_use_messages_included(self) -> None:
        """Tool use/result blocks contribute to token estimation."""
        msgs = [Message.tool_result("call-1", "some output text")]
        tokens = estimate_tokens(msgs)
        assert tokens > 0

    def test_multiple_messages(self) -> None:
        msgs = _make_messages(10)
        tokens = estimate_tokens(msgs)
        assert tokens > 0


# ------------------------------------------------------------------
# should_auto_compact
# ------------------------------------------------------------------


class TestShouldAutoCompact:
    """Tests for the auto-compact threshold check."""

    def test_below_threshold(self) -> None:
        state = CompactState()
        # 1000 tokens, 32K window → way below threshold
        assert not should_auto_compact(1000, 32_768, state)

    def test_above_threshold(self) -> None:
        state = CompactState()
        threshold = 32_768 - AUTOCOMPACT_BUFFER_TOKENS
        assert should_auto_compact(threshold, 32_768, state)

    def test_exactly_at_threshold(self) -> None:
        state = CompactState()
        threshold = 32_768 - AUTOCOMPACT_BUFFER_TOKENS
        assert should_auto_compact(threshold, 32_768, state)

    def test_circuit_breaker_disabled(self) -> None:
        """Disabled state should prevent auto-compact."""
        state = CompactState(is_disabled=True)
        assert not should_auto_compact(999_999, 32_768, state)

    def test_circuit_breaker_not_yet_tripped(self) -> None:
        """Failures below max should still allow auto-compact."""
        state = CompactState(consecutive_failures=MAX_CONSECUTIVE_FAILURES - 1)
        threshold = 32_768 - AUTOCOMPACT_BUFFER_TOKENS
        assert should_auto_compact(threshold, 32_768, state)


# ------------------------------------------------------------------
# resolve_context_window
# ------------------------------------------------------------------


class TestResolveContextWindow:
    """Tests for the 3-level context window resolution."""

    def test_config_override_takes_priority(self) -> None:
        assert resolve_context_window(65_536, 32_768) == 65_536

    def test_provider_api_used_when_no_config(self) -> None:
        assert resolve_context_window(None, 128_000) == 128_000

    def test_default_fallback(self) -> None:
        assert resolve_context_window(None, None) == DEFAULT_CONTEXT_WINDOW

    def test_zero_config_ignored(self) -> None:
        assert resolve_context_window(0, 32_768) == 32_768

    def test_zero_provider_ignored(self) -> None:
        assert resolve_context_window(None, 0) == DEFAULT_CONTEXT_WINDOW

    def test_negative_values_ignored(self) -> None:
        assert resolve_context_window(-1, -1) == DEFAULT_CONTEXT_WINDOW


# ------------------------------------------------------------------
# compact()
# ------------------------------------------------------------------


class TestCompact:
    """Tests for the core compact() function."""

    @pytest.mark.asyncio
    async def test_basic_compaction(self) -> None:
        """Compaction produces a summary and reduces message count."""
        msgs = _make_messages(10)
        provider = FakeSummarizationProvider()

        result = await compact(msgs, provider)

        assert result.summary
        assert result.messages_summarized > 0
        assert result.messages_kept == MIN_MESSAGES_TO_KEEP
        assert result.pre_tokens > 0

    @pytest.mark.asyncio
    async def test_too_few_messages_skips(self) -> None:
        """If there are <= MIN_MESSAGES_TO_KEEP messages, skip compaction."""
        msgs = _make_messages(MIN_MESSAGES_TO_KEEP)
        provider = FakeSummarizationProvider()

        result = await compact(msgs, provider)

        assert result.summary == ""
        assert result.messages_summarized == 0
        assert result.messages_kept == len(msgs)

    @pytest.mark.asyncio
    async def test_empty_summary_raises(self) -> None:
        """Empty LLM output should raise CompactError."""
        msgs = _make_messages(10)
        provider = EmptySummaryProvider()

        with pytest.raises(CompactError, match="empty summary"):
            await compact(msgs, provider)

    @pytest.mark.asyncio
    async def test_compaction_preserves_recent(self) -> None:
        """The most recent messages should be in the kept portion."""
        msgs = _make_messages(10)
        provider = FakeSummarizationProvider()

        result = await compact(msgs, provider)

        assert result.messages_kept == MIN_MESSAGES_TO_KEEP


# ------------------------------------------------------------------
# build_post_compact_messages
# ------------------------------------------------------------------


class TestBuildPostCompactMessages:
    """Tests for building the replacement message list."""

    def test_produces_summary_plus_ack_plus_kept(self) -> None:
        kept = [Message.user("recent"), Message.assistant_text("reply")]
        result = build_post_compact_messages("summary text", kept)

        # summary_msg + ack_msg + 2 kept = 4
        assert len(result) == 4
        assert result[0].role == "user"
        assert "summary text" in result[0].content[0].text  # type: ignore[union-attr]
        assert result[1].role == "assistant"

    def test_empty_kept_messages(self) -> None:
        result = build_post_compact_messages("summary", [])
        # Just the summary pair
        assert len(result) == 2

    def test_alternating_roles(self) -> None:
        """Result should maintain user/assistant alternation."""
        kept = _make_messages(4)
        result = build_post_compact_messages("summary", kept)

        # First: user (summary), second: assistant (ack)
        assert result[0].role == "user"
        assert result[1].role == "assistant"


# ------------------------------------------------------------------
# CompactState
# ------------------------------------------------------------------


class TestCompactState:
    """Tests for the per-session compaction state."""

    def test_default_state(self) -> None:
        state = CompactState()
        assert state.consecutive_failures == 0
        assert not state.is_disabled
        assert state.last_known_input_tokens == 0

    def test_circuit_breaker_trips(self) -> None:
        state = CompactState()
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            state.consecutive_failures += 1
        state.is_disabled = state.consecutive_failures >= MAX_CONSECUTIVE_FAILURES
        assert state.is_disabled

    def test_reset_after_success(self) -> None:
        state = CompactState(consecutive_failures=2)
        state.consecutive_failures = 0
        assert state.consecutive_failures == 0
        assert not state.is_disabled
