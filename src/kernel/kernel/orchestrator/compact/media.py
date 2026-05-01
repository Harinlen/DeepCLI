"""Emergency media stripping for provider media-size failures."""

from __future__ import annotations

import logging

from kernel.llm.types import ImageContent, Message, TextContent, ToolResultContent, UserMessage
from kernel.orchestrator.history import ConversationHistory

logger = logging.getLogger(__name__)


def strip_media(history: ConversationHistory) -> int:
    """Replace all image blocks in history with text placeholders.

    Args:
        history: Mutable conversation history that triggered a media-size error.

    Returns:
        Number of image blocks stripped.

    This pass is intentionally emergency-only: it preserves text/tool structure
    while removing payloads that a provider has already rejected.
    """
    messages: list[Message] = history.messages
    stripped = 0

    for idx, msg in enumerate(messages):
        if not isinstance(msg, UserMessage):
            continue
        new_content: list[TextContent | ImageContent | ToolResultContent] = []
        changed = False
        for block in msg.content:
            if isinstance(block, ImageContent):
                new_content.append(TextContent(text="[image removed — media size limit]"))
                stripped += 1
                changed = True
            elif isinstance(block, ToolResultContent) and isinstance(block.content, list):
                new_block, removed = _strip_tool_result_media(block)
                new_content.append(new_block)
                stripped += removed
                changed = changed or removed > 0
            elif isinstance(block, (TextContent, ImageContent, ToolResultContent)):
                new_content.append(block)
        if changed:
            messages[idx] = UserMessage(content=new_content)

    if stripped > 0:
        history._token_count = history._estimate_tokens_for(messages)
        logger.info("Compactor: stripped %d image(s) due to media size limit", stripped)
    return stripped


def _strip_tool_result_media(block: ToolResultContent) -> tuple[ToolResultContent, int]:
    """Strip images nested inside a tool result without changing its id.

    Args:
        block: Tool result whose content is known to be a content-block list.

    Returns:
        Updated tool result plus the number of nested images replaced.
    """
    new_sub: list[TextContent | ImageContent] = []
    stripped = 0
    for sub in block.content:
        if isinstance(sub, ImageContent):
            new_sub.append(TextContent(text="[image removed — media size limit]"))
            stripped += 1
        elif isinstance(sub, TextContent):
            new_sub.append(sub)
    if stripped == 0:
        return block, 0
    return (
        ToolResultContent(
            tool_use_id=block.tool_use_id,
            content=new_sub,
            is_error=block.is_error,
        ),
        stripped,
    )
