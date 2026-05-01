"""Compactor facade for ConversationHistory compression layers."""

from __future__ import annotations

import logging

from kernel.llm.types import Message
from kernel.orchestrator.compact.media import strip_media
from kernel.orchestrator.compact.microcompact import remove_read_only_pairs
from kernel.orchestrator.compact.snip import snip_read_only_results
from kernel.orchestrator.compact.summarize import Summarizer
from kernel.orchestrator.history import ConversationHistory

logger = logging.getLogger(__name__)

# Recent turns are kept verbatim so the model can resolve pronouns, pending tool
# results, and just-issued user instructions after compaction.
DEFAULT_KEEP_RECENT_TURNS = 5


class Compactor:
    """Compacts a ``ConversationHistory`` using cheap passes and LLM summary.

    The passes are ordered from loss-minimising to most lossy: media stripping
    for provider failures, snipping bulky read-only results, removing entire
    read-only pairs, then LLM summarisation of old messages.
    """

    def __init__(
        self,
        deps: object,
        model: object,
        keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
    ) -> None:
        """Create the compaction facade.

        Args:
            deps: Orchestrator dependency bundle or a minimal test double.
            model: Default model reference used when no compact role exists.
            keep_recent_turns: Number of recent user turns preserved verbatim.
        """
        self._deps = deps
        self._keep_recent = keep_recent_turns
        self._summarizer = Summarizer(deps, model)
        self._model = self._summarizer._model

    def strip_media(self, history: ConversationHistory) -> int:
        """Replace all ImageContent blocks in history.

        Args:
            history: Mutable conversation history.

        Returns:
            Number of image blocks replaced with placeholders.
        """
        return strip_media(history)

    def snip(self, history: ConversationHistory) -> int:
        """Replace read-only tool results in non-tail messages.

        Args:
            history: Mutable conversation history.

        Returns:
            Approximate number of characters removed from old tool results.
        """
        return snip_read_only_results(history, keep_recent_turns=self._keep_recent)

    def microcompact(self, history: ConversationHistory) -> int:
        """Remove entire read-only assistant + tool_result pairs.

        Args:
            history: Mutable conversation history.

        Returns:
            Number of assistant/tool-result pairs removed.
        """
        return remove_read_only_pairs(history, keep_recent_turns=self._keep_recent)

    async def compact(self, history: ConversationHistory) -> None:
        """Summarise old messages and replace them with a compacted header.

        Args:
            history: Mutable conversation history.

        Returns:
            ``None``.

        The method mutates ``history`` atomically after the summary is available
        so Session persistence can emit one replacement snapshot.
        """
        boundary = history.find_compaction_boundary(keep_recent_turns=self._keep_recent)
        if boundary == 0:
            logger.debug("Compactor: boundary=0, nothing to compact")
            return

        messages_to_summarise: list[Message] = history.messages[:boundary]
        if not messages_to_summarise:
            return

        summary = await self._summarizer.summarise(messages_to_summarise)
        prompts = getattr(self._deps, "prompts", None)
        summary_header = (
            prompts.render("orchestrator/summary_header", summary=summary)
            if prompts is not None
            else None
        )
        history.replace_with_compacted(
            summary=summary,
            boundary=boundary,
            summary_header=summary_header,
        )
        logger.info(
            "Compactor: compacted %d messages into summary (%d chars)",
            boundary,
            len(summary),
        )
