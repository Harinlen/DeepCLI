"""Rendering helpers for compaction prompts."""

from __future__ import annotations

from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
    UserMessage,
)
from kernel.orchestrator.history import ConversationHistory


def content_char_count(content: str | list[TextContent | ImageContent]) -> int:
    """Count characters in a ToolResultContent.content value.

    Args:
        content: String or content-block list stored in a tool result.

    Returns:
        Number of visible text characters.

    Image payloads count as zero because this estimate is used only to decide
    whether text-heavy read results are worth snipping.
    """
    if isinstance(content, str):
        return len(content)
    return sum(len(getattr(block, "text", "")) for block in content)


def is_read_only_assistant(msg: AssistantMessage, history: ConversationHistory) -> bool:
    """True if every content block is a read-only ToolUseContent.

    Args:
        msg: Assistant message being considered for removal.
        history: History object containing tool-kind metadata.

    Returns:
        ``True`` when the full assistant turn contains only read-only tool uses.

    A mixed assistant message is kept intact; removing only part of an assistant
    turn would make provider replay harder to reason about.
    """
    if not msg.content:
        return False
    for block in msg.content:
        if not isinstance(block, ToolUseContent):
            return False
        kind = history.tool_kind_for(block.id)
        if kind is None or not kind.is_read_only:
            return False
    return True


def is_tool_result_only(msg: UserMessage) -> bool:
    """True if the message contains only ToolResultContent blocks.

    Args:
        msg: User message following an assistant tool-use turn.

    Returns:
        ``True`` when every block is a tool result.

    The microcompactor uses this to remove complete assistant/result pairs
    without crossing into ordinary user text.
    """
    return bool(msg.content) and all(isinstance(b, ToolResultContent) for b in msg.content)


def render_messages(messages: list[Message]) -> str:
    """Render messages as plain text for the summarisation prompt.

    Args:
        messages: Conversation prefix selected for summarisation.

    Returns:
        Plain-text transcript suitable for the compaction prompt.

    The renderer favors compact, readable hints over lossless serialization; the
    summary LLM needs continuity facts, not a byte-perfect transcript.
    """
    lines: list[str] = []
    for msg in messages:
        role = "User" if isinstance(msg, UserMessage) else "Assistant"
        parts: list[str] = []
        for block in msg.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
            elif isinstance(block, ThinkingContent):
                parts.append(f"[thinking: {block.thinking[:200]}…]")
            elif isinstance(block, ToolUseContent):
                parts.append(f"[tool_use: {block.name}({block.input})]")
            elif isinstance(block, ToolResultContent):
                content = block.content
                text = content if isinstance(content, str) else str(content)[:200]
                parts.append(f"[tool_result: {text}]")
        if parts:
            lines.append(f"{role}: {'  '.join(parts)}")
    return "\n".join(lines)
