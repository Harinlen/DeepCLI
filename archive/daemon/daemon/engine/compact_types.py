"""Compaction data types, constants, and pure helpers.

Split out of :mod:`daemon.engine.compact` (which owns the LLM-call
:func:`compact` coroutine) so the purely-declarative pieces —
constants, state dataclasses, token-estimate + threshold logic,
context-window resolution — can be imported without dragging in
provider dependencies.

All names are re-exported from :mod:`daemon.engine.compact` for
backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from daemon.providers.base import Message, TextContent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Auto-compact fires when tokens >= context_window - this buffer.
AUTOCOMPACT_BUFFER_TOKENS = 13_000

# Default context window when neither config nor provider API provides one.
DEFAULT_CONTEXT_WINDOW = 32_768

# Max output tokens for the summarization LLM call.
COMPACT_MAX_OUTPUT_TOKENS = 8_000

# Minimum messages to keep (untouched) after compaction — 2 full turns.
MIN_MESSAGES_TO_KEEP = 4

# Circuit breaker: stop auto-compact after this many consecutive failures.
MAX_CONSECUTIVE_FAILURES = 3


# ---------------------------------------------------------------------------
# Compact state (per-session, lives on Orchestrator)
# ---------------------------------------------------------------------------


@dataclass
class CompactState:
    """Tracks per-session compaction state and circuit breaker.

    Attributes:
        consecutive_failures: How many auto-compact attempts have failed
            in a row.
        is_disabled: Set to ``True`` once the circuit breaker trips —
            auto-compact will not fire again for this session.
        last_known_input_tokens: The ``input_tokens`` value from the
            most recent ``StreamEnd``.  Used as the primary signal for
            the auto-compact threshold check.
    """

    consecutive_failures: int = 0
    is_disabled: bool = False
    last_known_input_tokens: int = 0


# ---------------------------------------------------------------------------
# Compaction result
# ---------------------------------------------------------------------------


@dataclass
class CompactionResult:
    """Returned by :func:`compact` with stats and the summary text.

    Attributes:
        summary: LLM-generated structured summary of the old messages.
        messages_summarized: Number of messages that were replaced.
        messages_kept: Number of recent messages preserved verbatim.
        pre_tokens: Estimated tokens before compaction.
        post_tokens: Estimated tokens after compaction.
    """

    summary: str
    messages_summarized: int = 0
    messages_kept: int = 0
    pre_tokens: int = 0
    post_tokens: int = 0


# ---------------------------------------------------------------------------
# Token estimation + threshold policy
# ---------------------------------------------------------------------------


def estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate for a list of messages.

    Uses the heuristic ``text_length / 3`` (i.e. ~4 chars/token with
    a 4/3 safety padding).  This intentionally over-estimates so that
    compaction triggers slightly early rather than too late.

    Args:
        messages: Conversation messages to estimate.

    Returns:
        Estimated token count (always >= 0).
    """
    total_chars = 0
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextContent):
                total_chars += len(block.text)
            else:
                # tool_use / tool_result — serialize to rough length.
                total_chars += len(str(block.model_dump()))
    # ~4 chars per token, padded by 4/3 → divide by 3.
    return max(total_chars // 3, 0)


def should_auto_compact(
    token_count: int,
    context_window: int,
    state: CompactState,
) -> bool:
    """Decide whether auto-compact should fire.

    Args:
        token_count: Current estimated (or real) input token count.
        context_window: The model's context window size.
        state: Per-session compact state (checked for circuit breaker).

    Returns:
        ``True`` if compaction should run.
    """
    if state.is_disabled:
        return False
    threshold = context_window - AUTOCOMPACT_BUFFER_TOKENS
    return token_count >= threshold


# ---------------------------------------------------------------------------
# Post-compact message construction
# ---------------------------------------------------------------------------


def build_post_compact_messages(
    summary: str,
    kept_messages: list[Message],
) -> list[Message]:
    """Build the replacement message list after compaction.

    Wraps the summary in a user + assistant pair to maintain
    the alternating role pattern that LLM APIs require, then
    appends the preserved recent messages.

    Args:
        summary: LLM-generated summary text.
        kept_messages: Recent messages to preserve verbatim.

    Returns:
        New message list ready to replace the conversation.
    """
    summary_msg = Message.user(
        f"[Previous conversation summary]\n\n{summary}\n\n"
        "(The conversation above has been compressed. "
        "This is a summary of the earlier context.)"
    )
    ack_msg = Message.assistant_text(
        "Understood. I have the context from the previous conversation. Please continue."
    )
    return [summary_msg, ack_msg, *kept_messages]


# ---------------------------------------------------------------------------
# Context window resolution
# ---------------------------------------------------------------------------


def resolve_context_window(
    config_value: int | None,
    provider_value: int | None,
) -> int:
    """Determine the effective context window size.

    Priority: user config > provider API query > default.

    Args:
        config_value: From ``ProviderSourceConfig.context_window``.
        provider_value: From provider's ``/v1/models`` API response.

    Returns:
        Context window size in tokens.
    """
    if config_value is not None and config_value > 0:
        return config_value
    if provider_value is not None and provider_value > 0:
        return provider_value
    return DEFAULT_CONTEXT_WINDOW


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


# Tools whose results can be safely truncated (content replaced with placeholder).
SNIPPABLE_TOOLS = frozenset({
    "file_read", "glob", "grep", "web_fetch", "web_search",
})

# Tools that are read-only — entire call rounds can be removed.
READ_ONLY_TOOLS = frozenset({
    "file_read", "glob", "grep", "web_fetch", "web_search",
})


class CompactError(Exception):
    """Raised when context compaction fails."""


__all__ = [
    "AUTOCOMPACT_BUFFER_TOKENS",
    "COMPACT_MAX_OUTPUT_TOKENS",
    "DEFAULT_CONTEXT_WINDOW",
    "MAX_CONSECUTIVE_FAILURES",
    "MIN_MESSAGES_TO_KEEP",
    "CompactError",
    "CompactState",
    "CompactionResult",
    "build_post_compact_messages",
    "estimate_tokens",
    "resolve_context_window",
    "should_auto_compact",
]
