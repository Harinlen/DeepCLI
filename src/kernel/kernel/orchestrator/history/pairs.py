"""Tool-use pairing helpers for conversation history."""

from __future__ import annotations

from typing import cast

from kernel.llm.types import AssistantMessage, Message, ToolResultContent, ToolUseContent


def pending_tool_use_ids(messages: list[Message]) -> list[str]:
    """Return tool_use IDs from the last assistant message without results.

    Args:
        messages: Conversation history in provider-neutral message order.

    Returns:
        Tool-use ids that still need matching ``ToolResultContent`` blocks.

    The scan stops at the last assistant turn because providers require results
    only for tool uses emitted by the immediately preceding assistant message.
    """
    last_assistant: AssistantMessage | None = None
    last_assistant_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        if isinstance(messages[idx], AssistantMessage):
            last_assistant = cast(AssistantMessage, messages[idx])
            last_assistant_idx = idx
            break

    if last_assistant is None:
        return []

    tool_use_ids = [b.id for b in last_assistant.content if isinstance(b, ToolUseContent)]
    if not tool_use_ids:
        return []

    answered: set[str] = set()
    for msg in messages[last_assistant_idx + 1 :]:
        for block in msg.content:
            if isinstance(block, ToolResultContent):
                answered.add(block.tool_use_id)

    return [tool_id for tool_id in tool_use_ids if tool_id not in answered]
