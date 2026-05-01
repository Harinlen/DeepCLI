"""Tests for reactive compaction — PromptTooLongError detection and recovery.

Covers:
- PromptTooLongError exception with tokens_over attribute
- Provider-level context-overflow detection helpers
- Compactor.reactive_compact() (emergency compaction)
- Orchestrator retry logic (prompt_too_long → compact → retry)
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from daemon.engine.orchestrator import Orchestrator
from daemon.engine.orchestrator.compactor import Compactor
from daemon.engine.stream import (
    CompactNotification,
    StreamEnd,
    StreamError,
    StreamEvent,
    TextDelta,
    UsageInfo,
)
from daemon.errors import PromptTooLongError, ProviderError
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class FakeSummarizingProvider(Provider):
    """Provider that returns a fixed summary (for compaction calls)."""

    name = "fake_summarizer"

    def __init__(self, summary: str = "## 1. Summary\nCompacted.") -> None:
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
        yield StreamEnd(usage=UsageInfo(input_tokens=50, output_tokens=30))

    async def models(self) -> list[ModelInfo]:
        return []


class PromptTooLongThenOkProvider(Provider):
    """Provider that raises PromptTooLongError on the first N calls.

    After ``fail_count`` failures, returns a normal text response.
    Even-numbered calls (used for compaction summarization) after
    the failure always return a summary.
    """

    name = "ptl_then_ok"

    def __init__(self, fail_count: int = 1) -> None:
        self._fail_count = fail_count
        self._call_count = 0
        self._fail_calls_remaining = fail_count

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        self._call_count += 1

        # If we still have failures to deliver AND this looks like a
        # main query (has system prompt — compaction does too, but we
        # alternate: fail → summary → fail → summary → ok).
        if self._fail_calls_remaining > 0 and self._call_count % 2 == 1:
            self._fail_calls_remaining -= 1
            raise PromptTooLongError("prompt is too long")

        # Compaction call or post-recovery call.
        if self._fail_calls_remaining > 0:
            # Still in retry phase — this is a compaction call.
            yield TextDelta(content="## Summary\nCompacted context.")
            yield StreamEnd(usage=UsageInfo(input_tokens=50, output_tokens=30))
        else:
            # All failures exhausted — return success.
            yield TextDelta(content="Recovered!")
            yield StreamEnd(usage=UsageInfo(input_tokens=100, output_tokens=10))

    async def models(self) -> list[ModelInfo]:
        return []


class AlwaysPromptTooLongProvider(Provider):
    """Provider that raises PromptTooLongError on odd calls.

    Even calls return a compaction summary.  The main query (always
    odd-numbered) never succeeds within the retry budget.
    """

    name = "always_ptl"

    def __init__(self) -> None:
        self._call_count = 0

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        self._call_count += 1
        if self._call_count % 2 == 1:
            raise PromptTooLongError("prompt is too long", tokens_over=5000)
        # Compaction call.
        yield TextDelta(content="## Summary\nCompacted.")
        yield StreamEnd(usage=UsageInfo(input_tokens=50, output_tokens=20))

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


def _make_orchestrator(
    provider: Provider,
    tmp_path: object,
) -> Orchestrator:
    """Create an Orchestrator with the given provider."""
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    return make_test_orchestrator(provider, tmp_path)


async def _collect_events(
    orch: Orchestrator,
    text: str,
) -> list[StreamEvent]:
    """Collect all events from a query."""
    events: list[StreamEvent] = []
    async for event in orch.query(text):
        events.append(event)
    return events


# ------------------------------------------------------------------
# PromptTooLongError
# ------------------------------------------------------------------


class TestPromptTooLongError:
    """Tests for the PromptTooLongError exception."""

    def test_is_provider_error(self) -> None:
        """PromptTooLongError is a subclass of ProviderError."""
        assert issubclass(PromptTooLongError, ProviderError)

    def test_message_preserved(self) -> None:
        exc = PromptTooLongError("too long")
        assert str(exc) == "too long"

    def test_tokens_over_default_none(self) -> None:
        exc = PromptTooLongError("too long")
        assert exc.tokens_over is None

    def test_tokens_over_set(self) -> None:
        exc = PromptTooLongError("too long", tokens_over=5000)
        assert exc.tokens_over == 5000


# ------------------------------------------------------------------
# Provider context-overflow detection
# ------------------------------------------------------------------


class TestOpenAIContextOverflow:
    """Tests for _is_context_overflow (OpenAI-compatible)."""

    def test_maximum_context_length(self) -> None:
        from openai import BadRequestError

        from daemon.providers.openai_base import _is_context_overflow

        exc = BadRequestError(
            message="This model's maximum context length is 128000 tokens",
            response=_fake_response(400),
            body=None,
        )
        assert _is_context_overflow(exc) is True

    def test_normal_error_not_overflow(self) -> None:
        from openai import BadRequestError

        from daemon.providers.openai_base import _is_context_overflow

        exc = BadRequestError(
            message="Invalid request: missing model field",
            response=_fake_response(400),
            body=None,
        )
        assert _is_context_overflow(exc) is False


class TestAnthropicContextOverflow:
    """Tests for _is_anthropic_context_overflow."""

    def test_prompt_too_long(self) -> None:
        from anthropic import BadRequestError

        from daemon.providers.anthropic import _is_anthropic_context_overflow

        exc = BadRequestError(
            message="prompt is too long: 250000 tokens > 200000 maximum",
            response=_fake_response(400),
            body={"type": "error", "error": {"type": "invalid_request_error"}},
        )
        assert _is_anthropic_context_overflow(exc) is True

    def test_normal_error_not_overflow(self) -> None:
        from anthropic import BadRequestError

        from daemon.providers.anthropic import _is_anthropic_context_overflow

        exc = BadRequestError(
            message="Invalid API key",
            response=_fake_response(400),
            body={"type": "error", "error": {"type": "authentication_error"}},
        )
        assert _is_anthropic_context_overflow(exc) is False


def _fake_response(status_code: int) -> Any:
    """Create a minimal fake HTTP response for SDK error constructors."""
    import httpx

    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "https://api.example.com/v1"),
    )


# ------------------------------------------------------------------
# Compactor.reactive_compact()
# ------------------------------------------------------------------


class TestReactiveCompact:
    """Tests for Compactor.reactive_compact()."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Reactive compact succeeds and yields CompactNotification."""
        compactor = Compactor(context_window=32_768)
        from daemon.engine.conversation import Conversation

        conv = Conversation()
        for msg in _make_messages(10):
            if msg.role == "user":
                await conv.add_user_message(msg.content[0].text)  # type: ignore[union-attr]
            else:
                await conv.add_assistant_message(msg.content)

        provider = FakeSummarizingProvider()
        events: list[StreamEvent] = []
        async for evt in compactor.reactive_compact(conv, provider, None, None):
            events.append(evt)

        assert len(events) == 1
        assert isinstance(events[0], CompactNotification)
        assert events[0].messages_summarized > 0

    @pytest.mark.asyncio
    async def test_too_few_messages(self) -> None:
        """Reactive compact with too few messages yields StreamError."""
        compactor = Compactor(context_window=32_768)
        from daemon.engine.conversation import Conversation

        conv = Conversation()
        # Add fewer messages than MIN_MESSAGES_TO_KEEP + 1
        await conv.add_user_message("hi")
        await conv.add_assistant_message([Message.assistant_text("hello").content[0]])

        provider = FakeSummarizingProvider()
        events: list[StreamEvent] = []
        async for evt in compactor.reactive_compact(conv, provider, None, None):
            events.append(evt)

        assert len(events) == 1
        assert isinstance(events[0], StreamError)
        assert "too few" in events[0].message.lower()

    @pytest.mark.asyncio
    async def test_resets_failure_count(self) -> None:
        """Successful reactive compact resets consecutive_failures to 0."""
        compactor = Compactor(context_window=32_768)
        compactor.state.consecutive_failures = 2
        from daemon.engine.conversation import Conversation

        conv = Conversation()
        for msg in _make_messages(10):
            if msg.role == "user":
                await conv.add_user_message(msg.content[0].text)  # type: ignore[union-attr]
            else:
                await conv.add_assistant_message(msg.content)

        provider = FakeSummarizingProvider()
        async for _ in compactor.reactive_compact(conv, provider, None, None):
            pass

        assert compactor.state.consecutive_failures == 0


# ------------------------------------------------------------------
# Orchestrator reactive retry
# ------------------------------------------------------------------


class TestOrchestratorReactiveRetry:
    """Tests for orchestrator catching PromptTooLongError and retrying."""

    @staticmethod
    async def _prepopulate(orch: Orchestrator, n: int = 10) -> None:
        """Add enough messages so compaction has content to summarize."""
        for i in range(n):
            if i % 2 == 0:
                await orch.conversation.add_user_message(f"User message {i}")
            else:
                await orch.conversation.add_assistant_message(
                    [Message.assistant_text(f"Assistant response {i}").content[0]]
                )

    @pytest.mark.asyncio
    async def test_single_failure_then_recovery(self, tmp_path: object) -> None:
        """PromptTooLongError on first call → compact → retry → success."""
        provider = PromptTooLongThenOkProvider(fail_count=1)
        orch = _make_orchestrator(provider, tmp_path)
        await self._prepopulate(orch)

        events = await _collect_events(orch, "hello")

        # Should see a CompactNotification from reactive compact, then
        # the recovered response.
        types = [type(e).__name__ for e in events]
        assert "CompactNotification" in types
        assert "TextDelta" in types
        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert text_events[0].content == "Recovered!"

    @pytest.mark.asyncio
    async def test_double_failure_then_recovery(self, tmp_path: object) -> None:
        """Two consecutive PromptTooLongErrors → two compactions → success."""
        provider = PromptTooLongThenOkProvider(fail_count=2)
        orch = _make_orchestrator(provider, tmp_path)
        await self._prepopulate(orch)

        events = await _collect_events(orch, "hello")

        compact_events = [e for e in events if isinstance(e, CompactNotification)]
        assert len(compact_events) == 2
        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert text_events[0].content == "Recovered!"

    @pytest.mark.asyncio
    async def test_exceeded_max_retries(self, tmp_path: object) -> None:
        """Exhausting reactive retries yields StreamError."""
        provider = AlwaysPromptTooLongProvider()
        orch = _make_orchestrator(provider, tmp_path)
        await self._prepopulate(orch)

        events = await _collect_events(orch, "hello")

        error_events = [e for e in events if isinstance(e, StreamError)]
        assert len(error_events) >= 1
        assert "even after compaction" in error_events[-1].message.lower()
        assert any(isinstance(e, StreamEnd) for e in events)


# ------------------------------------------------------------------
# Re-exports check
# ------------------------------------------------------------------


class TestErrorReExports:
    """Verify PromptTooLongError is accessible from errors module."""

    def test_import(self) -> None:
        from daemon.errors import PromptTooLongError as Cls

        assert Cls is not None
        assert issubclass(Cls, ProviderError)
