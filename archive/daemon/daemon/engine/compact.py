"""Context compaction — LLM-powered conversation summarization.

Owns the :func:`compact` coroutine that makes an LLM call to
compress old messages into a structured summary.  The surrounding
concepts — state, thresholds, token estimation, result dataclass,
context-window resolution — live in :mod:`daemon.engine.compact_types`
and are re-exported from this module so existing
``from daemon.engine.compact import …`` imports keep working.

When the conversation token count approaches the model's context
window limit, the orchestrator calls :func:`compact` to compress
older messages, preserving key information while freeing space.
"""

from __future__ import annotations

import logging
from pathlib import Path

from daemon.engine.compact_types import (
    AUTOCOMPACT_BUFFER_TOKENS,
    COMPACT_MAX_OUTPUT_TOKENS,
    DEFAULT_CONTEXT_WINDOW,
    MAX_CONSECUTIVE_FAILURES,
    MIN_MESSAGES_TO_KEEP,
    READ_ONLY_TOOLS,
    SNIPPABLE_TOOLS,
    CompactError,
    CompactionResult,
    CompactState,
    build_post_compact_messages,
    estimate_tokens,
    resolve_context_window,
    should_auto_compact,
)
from daemon.providers.base import (
    Message,
    Provider,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# ---------------------------------------------------------------------------
# Summarization prompt
# ---------------------------------------------------------------------------

_COMPACT_SYSTEM_PROMPT = (
    (_PROMPTS_DIR / "compact_system.txt").read_text(encoding="utf-8").rstrip("\n")
)


def _serialize_messages_for_summary(messages: list[Message]) -> str:
    """Serialize messages into a human-readable format for the LLM.

    Each message is formatted as ``[Role]: content`` with truncation
    to keep the serialization itself within reasonable bounds.

    Args:
        messages: Messages to serialize (the "old" portion).

    Returns:
        Multi-line string ready to append to the summarization prompt.
    """
    lines: list[str] = []
    for msg in messages:
        role_label = msg.role.capitalize()
        for block in msg.content:
            if isinstance(block, TextContent):
                text = block.text[:500] + ("..." if len(block.text) > 500 else "")
                lines.append(f"[{role_label}]: {text}")
            else:
                # tool_use or tool_result — compact representation.
                dump = block.model_dump()
                btype = dump.get("type", "unknown")
                if btype == "tool_use":
                    name = dump.get("name", "?")
                    args_str = str(dump.get("arguments", {}))[:150]
                    lines.append(f"[Tool Call]: {name}({args_str})")
                elif btype == "tool_result":
                    output = str(dump.get("output", ""))[:200]
                    is_err = dump.get("is_error", False)
                    label = "Tool Error" if is_err else "Tool Result"
                    lines.append(f"[{label}]: {output}")
                else:
                    lines.append(f"[{role_label}]: {dump}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer 1: Snip — truncate old tool_result content (zero LLM cost)
# ---------------------------------------------------------------------------


def _build_tool_name_index(messages: list[Message]) -> dict[str, str]:
    """Map tool_call_id → tool_name by scanning ToolUseContent blocks.

    ToolResultContent only carries tool_call_id, not the tool name.
    This index lets us look up which tool produced a given result.
    """
    index: dict[str, str] = {}
    for msg in messages:
        for block in msg.content:
            if isinstance(block, ToolUseContent):
                index[block.tool_call_id] = block.name
    return index


def _estimate_content_chars(content: list) -> int:
    """Estimate total character count of message content blocks."""
    total = 0
    for block in content:
        if isinstance(block, TextContent):
            total += len(block.text)
        elif isinstance(block, ToolResultContent):
            total += len(block.output)
        else:
            total += len(str(block.model_dump()))
    return total


def snip_tool_results(
    messages: list[Message],
    protected_tail: int = MIN_MESSAGES_TO_KEEP,
) -> tuple[list[Message], int]:
    """Replace old tool_result content with placeholder (Layer 1).

    Only snips results from tools in SNIPPABLE_TOOLS.  Protected
    tail messages are never modified.

    Args:
        messages: Full conversation messages.
        protected_tail: Number of tail messages to protect.

    Returns:
        (new_messages, chars_freed) — new list with snipped results
        and total characters freed.
    """
    if len(messages) <= protected_tail:
        return messages, 0

    tool_index = _build_tool_name_index(messages)
    cutoff = len(messages) - protected_tail
    new_messages: list[Message] = []
    total_freed = 0

    for i, msg in enumerate(messages):
        if i >= cutoff:
            new_messages.append(msg)
            continue

        modified = False
        new_content = []
        for block in msg.content:
            if isinstance(block, ToolResultContent):
                tool_name = tool_index.get(block.tool_call_id, "")
                if tool_name in SNIPPABLE_TOOLS and len(block.output) > 100:
                    old_len = len(block.output)
                    placeholder = f"[result truncated — {old_len} chars]"
                    freed = old_len - len(placeholder)
                    if freed > 0:
                        total_freed += freed
                        new_content.append(
                            ToolResultContent(
                                tool_call_id=block.tool_call_id,
                                output=placeholder,
                                is_error=block.is_error,
                            )
                        )
                        modified = True
                        continue
            new_content.append(block)

        if modified:
            new_messages.append(Message(role=msg.role, content=new_content))
        else:
            new_messages.append(msg)

    return new_messages, total_freed


# ---------------------------------------------------------------------------
# Layer 2: Micro-compact — remove read-only tool rounds (zero LLM cost)
# ---------------------------------------------------------------------------


def micro_compact(
    messages: list[Message],
    protected_tail: int = MIN_MESSAGES_TO_KEEP,
) -> tuple[list[Message], int]:
    """Remove entire read-only tool call rounds from old messages (Layer 2).

    A "read-only round" is an assistant message where ALL tool_use
    blocks are for tools in READ_ONLY_TOOLS, followed by the
    corresponding tool-role result messages.

    Args:
        messages: Full conversation messages.
        protected_tail: Number of tail messages to protect.

    Returns:
        (new_messages, chars_freed) — new list with read-only rounds
        removed and total characters freed.
    """
    if len(messages) <= protected_tail:
        return messages, 0

    tool_index = _build_tool_name_index(messages)
    cutoff = len(messages) - protected_tail
    indices_to_remove: set[int] = set()
    total_freed = 0

    # Scan old messages for read-only assistant rounds.
    for i in range(cutoff):
        msg = messages[i]
        if msg.role != "assistant":
            continue

        tool_uses = [b for b in msg.content if isinstance(b, ToolUseContent)]
        if not tool_uses:
            continue

        # Check if ALL tool calls in this message are read-only.
        if not all(b.name in READ_ONLY_TOOLS for b in tool_uses):
            continue

        # Collect the tool_call_ids from this assistant message.
        call_ids = {b.tool_call_id for b in tool_uses}

        # Mark this assistant message for removal.
        indices_to_remove.add(i)
        total_freed += _estimate_content_chars(msg.content)

        # Find and mark the following tool-result messages that match.
        for j in range(i + 1, cutoff):
            result_msg = messages[j]
            if result_msg.role != "tool":
                break
            result_ids = {
                b.tool_call_id
                for b in result_msg.content
                if isinstance(b, ToolResultContent)
            }
            if result_ids & call_ids:
                indices_to_remove.add(j)
                total_freed += _estimate_content_chars(result_msg.content)
            else:
                break

    if not indices_to_remove:
        return messages, 0

    # Build new list, inserting a marker.
    new_messages: list[Message] = []
    marker_inserted = False
    for i, msg in enumerate(messages):
        if i in indices_to_remove:
            if not marker_inserted:
                new_messages.append(
                    Message.user(
                        f"[{len(indices_to_remove)} read-only tool messages removed]"
                    )
                )
                new_messages.append(
                    Message.assistant_text("Understood, continuing.")
                )
                marker_inserted = True
            continue
        new_messages.append(msg)

    return new_messages, total_freed


# ---------------------------------------------------------------------------
# Core compact logic (Layer 3 — LLM summarization)
# ---------------------------------------------------------------------------


async def compact(
    messages: list[Message],
    provider: Provider,
    model: str | None = None,
) -> CompactionResult:
    """Summarize old messages via an LLM call.

    Splits messages into *old* (to be summarized) and *kept* (recent,
    preserved verbatim).  Calls the LLM with the serialized old
    messages and returns a :class:`CompactionResult`.

    The caller (orchestrator) is responsible for replacing the
    conversation and writing the ``CompactBoundaryEntry``.

    Args:
        messages: Full conversation message list.
        provider: LLM provider for the summarization call.
        model: Model ID override (uses provider default if ``None``).

    Returns:
        A :class:`CompactionResult` with the summary and stats.

    Raises:
        CompactError: If the LLM call fails or returns empty output.
    """
    pre_tokens = estimate_tokens(messages)

    # Determine split point — keep at least MIN_MESSAGES_TO_KEEP.
    keep_count = max(MIN_MESSAGES_TO_KEEP, 0)
    if len(messages) <= keep_count:
        # Not enough messages to compact.
        return CompactionResult(
            summary="",
            messages_summarized=0,
            messages_kept=len(messages),
            pre_tokens=pre_tokens,
            post_tokens=pre_tokens,
        )

    old_messages = messages[:-keep_count]
    kept_messages = messages[-keep_count:]

    serialized = _serialize_messages_for_summary(old_messages)
    user_prompt = f"Conversation history:\n\n{serialized}"

    # Call the LLM for summarization (no tools, just text completion).
    summary_parts: list[str] = []

    from daemon.engine.context import PromptSection

    async for event in provider.stream(
        messages=[Message.user(user_prompt)],
        tools=None,
        model=model,
        system=[PromptSection(text=_COMPACT_SYSTEM_PROMPT)],
    ):
        if hasattr(event, "content") and hasattr(event, "type"):
            if event.type == "text_delta":  # type: ignore[union-attr]
                summary_parts.append(event.content)  # type: ignore[union-attr]

    summary = "".join(summary_parts).strip()
    if not summary:
        raise CompactError("LLM returned empty summary")

    # Build the post-compact message list to estimate new token count.
    new_messages = build_post_compact_messages(summary, kept_messages)
    post_tokens = estimate_tokens(new_messages)

    return CompactionResult(
        summary=summary,
        messages_summarized=len(old_messages),
        messages_kept=len(kept_messages),
        pre_tokens=pre_tokens,
        post_tokens=post_tokens,
    )


# ---------------------------------------------------------------------------
# Re-exports for back-compat
# ---------------------------------------------------------------------------

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
    "compact",
    "estimate_tokens",
    "resolve_context_window",
    "should_auto_compact",
]
