"""Context compaction subsystem.

Wraps :func:`daemon.engine.compact.compact` with per-session state
(token tracking, circuit breaker).  The :class:`Orchestrator` calls
:meth:`compact_if_needed` before each LLM round and
:meth:`force_compact` for the ``/compact`` CLI command.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, AsyncIterator

from daemon.engine.compact import compact, micro_compact, snip_tool_results
from daemon.engine.compact_types import (
    MAX_CONSECUTIVE_FAILURES,
    MIN_MESSAGES_TO_KEEP,
    CompactError,
    CompactionResult,
    CompactState,
    build_post_compact_messages,
    estimate_tokens,
    should_auto_compact,
)
from daemon.engine.conversation import Conversation
from daemon.engine.stream import (
    CompactNotification,
    StreamError,
    StreamEvent,
)
from daemon.errors import ProviderError
from daemon.extensions.hooks.base import HookContext, HookEvent
from daemon.extensions.hooks.registry import HookRegistry
from daemon.extensions.hooks.runner import run_hooks
from daemon.sessions.entry import CompactBoundaryEntry

if TYPE_CHECKING:
    from daemon.engine.orchestrator.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class Compactor:
    """Manages context compaction for a single session.

    Args:
        context_window: Initial context window size (0 = not resolved).
    """

    def __init__(
        self,
        context_window: int = 0,
        hook_registry: HookRegistry | None = None,
    ) -> None:
        self.state = CompactState()
        self.context_window = context_window
        self._hook_registry = hook_registry or HookRegistry()

    async def compact_if_needed(
        self,
        conversation: Conversation,
        provider: Any,
        model: str | None,
        on_entry: Callable[[Any], None] | None,
        memory_manager: MemoryManager | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Check token count and run compaction if above threshold.

        Uses a 3-layer cascade:
          1. **Snip** — truncate old tool_result content (zero LLM cost).
          2. **Micro-compact** — remove entire read-only rounds (zero LLM cost).
          3. **Full** — LLM summarization (existing behavior).

        Yields:
            ``CompactNotification`` for each layer that fires.
        """
        # Use real token count if available, else estimate.
        if self.state.last_known_input_tokens > 0:
            token_count = self.state.last_known_input_tokens
        else:
            token_count = estimate_tokens(conversation.get_messages())

        if not should_auto_compact(token_count, self.context_window, self.state):
            return

        logger.info(
            "Auto-compact triggered: %d tokens (threshold=%d)",
            token_count,
            self.context_window,
        )

        # Layer 1: Snip tool results.
        messages = conversation.get_messages()
        snipped, chars_freed = snip_tool_results(messages, MIN_MESSAGES_TO_KEEP)
        if chars_freed > 0:
            tokens_freed = max(chars_freed // 3, 0)  # same heuristic as estimate_tokens
            await conversation.replace_messages(snipped)
            self.state.last_known_input_tokens = max(0, token_count - tokens_freed)
            logger.info("Snip freed ~%d chars (~%d tokens)", chars_freed, tokens_freed)
            yield CompactNotification(
                summary_preview=f"Snipped tool results ({chars_freed} chars)",
                messages_summarized=0,
                strategy="snip",
                tokens_freed=tokens_freed,
            )
            if not should_auto_compact(
                self.state.last_known_input_tokens, self.context_window, self.state
            ):
                return
            messages = snipped

        # Layer 2: Micro-compact (remove read-only rounds).
        compacted, chars_freed_2 = micro_compact(messages, MIN_MESSAGES_TO_KEEP)
        if chars_freed_2 > 0:
            tokens_freed_2 = max(chars_freed_2 // 3, 0)
            await conversation.replace_messages(compacted)
            self.state.last_known_input_tokens = max(
                0, self.state.last_known_input_tokens - tokens_freed_2
            )
            logger.info("Micro-compact freed ~%d chars (~%d tokens)", chars_freed_2, tokens_freed_2)
            yield CompactNotification(
                summary_preview=f"Removed read-only tool rounds ({chars_freed_2} chars)",
                messages_summarized=0,
                strategy="micro",
                tokens_freed=tokens_freed_2,
            )
            if not should_auto_compact(
                self.state.last_known_input_tokens, self.context_window, self.state
            ):
                return

        # Layer 3: Full LLM compact.
        try:
            result = await self._run(conversation, provider, model, on_entry, memory_manager)
            self.state.consecutive_failures = 0
            yield CompactNotification(
                summary_preview=result.summary[:200],
                messages_summarized=result.messages_summarized,
                strategy="full",
            )
        except Exception:
            logger.exception("Auto-compact failed")
            self.state.consecutive_failures += 1
            if self.state.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self.state.is_disabled = True
                logger.warning(
                    "Auto-compact disabled after %d consecutive failures",
                    MAX_CONSECUTIVE_FAILURES,
                )

    async def reactive_compact(
        self,
        conversation: Conversation,
        provider: Any,
        model: str | None,
        on_entry: Callable[[Any], None] | None,
        memory_manager: MemoryManager | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Emergency compaction triggered by ``PromptTooLongError``.

        Runs unconditionally (bypasses threshold and circuit breaker).
        The caller is responsible for retry-count limits.

        Yields:
            ``CompactNotification`` on success, ``StreamError`` on failure.
        """
        if conversation.message_count < MIN_MESSAGES_TO_KEEP + 1:
            yield StreamError(message="Cannot compact — too few messages.")
            return

        logger.info("Reactive compact triggered (prompt_too_long recovery)")

        try:
            result = await self._run(conversation, provider, model, on_entry, memory_manager)
            self.state.consecutive_failures = 0
            yield CompactNotification(
                summary_preview=result.summary[:200],
                messages_summarized=result.messages_summarized,
            )
        except Exception:
            logger.exception("Reactive compact failed")
            yield StreamError(message="Reactive compaction failed — context too large.")

    async def force_compact(
        self,
        conversation: Conversation,
        provider: Any,
        model: str | None,
        on_entry: Callable[[Any], None] | None,
        memory_manager: MemoryManager | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Manually trigger compaction (``/compact``), ignoring circuit breaker.

        Yields:
            ``CompactNotification`` on success, ``StreamError`` on failure.
        """
        if conversation.message_count < MIN_MESSAGES_TO_KEEP + 1:
            yield StreamError(message="Not enough messages to compact.")
            return

        try:
            result = await self._run(conversation, provider, model, on_entry, memory_manager)
            yield CompactNotification(
                summary_preview=result.summary[:200],
                messages_summarized=result.messages_summarized,
            )
        except (CompactError, ProviderError) as exc:
            yield StreamError(message=f"Compaction failed: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error during manual compact")
            yield StreamError(message=f"Compaction failed: {exc}")

    async def _fire_compact_hook(self, event: HookEvent, **kwargs: Any) -> None:
        """Fire pre/post compact hook, swallowing errors."""
        try:
            hooks = self._hook_registry.get_hooks(event)
            if hooks:
                ctx = HookContext(**kwargs)
                await run_hooks(hooks, ctx)
        except Exception:
            logger.exception("Error running %s hook", event.value)

    async def _run(
        self,
        conversation: Conversation,
        provider: Any,
        model: str | None,
        on_entry: Callable[[Any], None] | None,
        memory_manager: MemoryManager | None = None,
    ) -> CompactionResult:
        """Execute compaction: summarize old messages and replace conversation."""
        messages = conversation.get_messages()

        # Fire pre_compact hook.
        await self._fire_compact_hook(
            HookEvent.PRE_COMPACT,
            message_count=len(messages),
            token_estimate=self.state.last_known_input_tokens or estimate_tokens(messages),
        )

        result = await compact(messages, provider, model)

        if not result.summary:
            raise CompactError("Empty compaction result")

        # Append hot memory bodies (Phase 5.7D).
        summary = result.summary
        if memory_manager is not None:
            hot_suffix = memory_manager.build_hot_memory_suffix()
            if hot_suffix:
                summary = f"{summary}\n\n{hot_suffix}"

        # Replace conversation messages.
        keep_count = max(MIN_MESSAGES_TO_KEEP, 0)
        kept = messages[-keep_count:] if len(messages) > keep_count else messages
        new_messages = build_post_compact_messages(summary, kept)
        await conversation.replace_messages(new_messages)

        # Reset token tracking.
        self.state.last_known_input_tokens = 0

        # Persist compact boundary to transcript.
        if on_entry:
            on_entry(
                CompactBoundaryEntry(
                    summary=result.summary,
                    preserved_count=result.messages_kept,
                )
            )

        logger.info(
            "Compaction complete: %d messages summarized, %d kept, ~%d→%d tokens",
            result.messages_summarized,
            result.messages_kept,
            result.pre_tokens,
            result.post_tokens,
        )

        # Fire post_compact hook.
        await self._fire_compact_hook(
            HookEvent.POST_COMPACT,
            messages_removed=result.messages_summarized,
            summary_tokens=result.post_tokens,
        )

        return result
