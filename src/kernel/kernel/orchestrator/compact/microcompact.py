"""Read-only tool-pair microcompaction."""

from __future__ import annotations

import logging

from kernel.llm.types import AssistantMessage, TextContent, UserMessage
from kernel.orchestrator.compact.render import is_read_only_assistant, is_tool_result_only
from kernel.orchestrator.history import ConversationHistory

logger = logging.getLogger(__name__)


def remove_read_only_pairs(history: ConversationHistory, *, keep_recent_turns: int) -> int:
    """Remove entire read-only assistant + tool_result pairs from non-tail.

    Args:
        history: Mutable conversation history.
        keep_recent_turns: Number of recent user turns that must remain verbatim.

    Returns:
        Number of assistant/tool-result pairs removed.

    Read-only pairs are safe to remove because their durable effect is already
    represented by later model context or by files on disk; mutating tools are
    never removed by this pass.
    """
    boundary = history.find_compaction_boundary(keep_recent_turns=keep_recent_turns)
    if boundary <= 1:
        return 0

    messages = history.messages
    indices_to_remove: set[int] = set()
    idx = 0
    while idx < boundary - 1:
        msg = messages[idx]
        next_msg = messages[idx + 1]
        # Providers require every tool_use to have a matching tool_result in the
        # immediate replay window.  We remove both sides together to preserve
        # that invariant.
        if (
            isinstance(msg, AssistantMessage)
            and is_read_only_assistant(msg, history)
            and isinstance(next_msg, UserMessage)
            and is_tool_result_only(next_msg)
        ):
            indices_to_remove.update({idx, idx + 1})
            idx += 2
        else:
            idx += 1

    if not indices_to_remove:
        return 0

    pair_count = len(indices_to_remove) // 2
    marker = UserMessage(content=[TextContent(text=f"[{pair_count} read-only tool calls removed]")])
    kept_pre = [m for j, m in enumerate(messages[:boundary]) if j not in indices_to_remove]
    kept_pre.insert(min(min(indices_to_remove), len(kept_pre)), marker)
    history._messages = kept_pre + messages[boundary:]
    history._token_count = history._estimate_tokens_for(history._messages)
    logger.info("Compactor: microcompacted %d read-only tool pairs", pair_count)
    return pair_count
