"""Token estimation helpers for conversation history."""

from __future__ import annotations

from kernel.llm.types import (
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)

# Rough English-heavy estimate used only before provider usage arrives.  The
# value deliberately errs simple because compaction thresholds already keep a
# safety margin below the real context window.
CHARS_PER_TOKEN = 4


def estimate_tokens_for(messages: list[Message]) -> int:
    """Rough token estimate for provider-neutral message history.

    Args:
        messages: Conversation messages in the internal LLM schema.

    Returns:
        At least one token, so empty histories never look like "unknown" when
        compared with compaction thresholds.
    """
    total_chars = 0
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextContent):
                total_chars += len(block.text)
            elif isinstance(block, ThinkingContent):
                total_chars += len(block.thinking)
            elif isinstance(block, ToolUseContent):
                total_chars += len(block.name) + len(str(block.input))
            elif isinstance(block, ToolResultContent):
                content = block.content
                if isinstance(content, str):
                    total_chars += len(content)
                else:
                    total_chars += sum(len(getattr(item, "text", "")) for item in content)
    return max(1, total_chars // CHARS_PER_TOKEN)
