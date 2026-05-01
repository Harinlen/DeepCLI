"""Read-only tool-result snipping for old history."""

from __future__ import annotations

import logging

from kernel.llm.types import ImageContent, TextContent, ToolResultContent, UserMessage
from kernel.orchestrator.compact.render import content_char_count
from kernel.orchestrator.history import ConversationHistory

logger = logging.getLogger(__name__)


def snip_read_only_results(history: ConversationHistory, *, keep_recent_turns: int) -> int:
    """Replace read-only tool results in non-tail messages with placeholders.

    Args:
        history: Mutable conversation history.
        keep_recent_turns: Number of recent user turns preserved exactly.

    Returns:
        Approximate number of characters freed.

    Snipping keeps the tool_result block itself so provider tool-use pairing
    remains valid even after old bulky read/search output is removed.
    """
    boundary = history.find_compaction_boundary(keep_recent_turns=keep_recent_turns)
    if boundary == 0:
        return 0

    freed = 0
    messages = history.messages
    for idx in range(boundary):
        msg = messages[idx]
        if not isinstance(msg, UserMessage):
            continue
        new_content: list[TextContent | ImageContent | ToolResultContent] = []
        changed = False
        for block in msg.content:
            if isinstance(block, ToolResultContent) and not block.is_error:
                kind = history.tool_kind_for(block.tool_use_id)
                if kind is not None and kind.is_read_only:
                    # Error results are never snipped; they often contain the
                    # reason the next assistant turn corrected course.
                    old_size = content_char_count(block.content)
                    if old_size > 0:
                        freed += old_size
                        block = ToolResultContent(
                            tool_use_id=block.tool_use_id,
                            content=f"[result snipped — {old_size} chars]",
                            is_error=False,
                        )
                        changed = True
            new_content.append(block)
        if changed:
            messages[idx] = UserMessage(content=new_content)

    if freed > 0:
        history._token_count = history._estimate_tokens_for(messages)
        logger.info("Compactor: snipped %d chars from read-only tool results", freed)
    return freed
