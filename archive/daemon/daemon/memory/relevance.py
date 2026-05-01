"""LLM-based memory relevance selector (Phase 5.7B).

Uses a lightweight LLM side-query to pick the top-K most relevant
memories for the current user message.  Only activated when the
memory index exceeds a configurable threshold (default 30 entries).

The selector sends the full frontmatter manifest (filename +
description + type) to the provider and asks it to return a JSON
array of filenames.  This mirrors Claude Code's ``findRelevantMemories``
approach — no TF-IDF, no embeddings.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from daemon.config.schema import MemoryRelevanceRuntimeConfig
from daemon.engine.stream import StreamEnd, TextDelta
from daemon.memory.schema import MemoryRecord
from daemon.providers.base import Message

if TYPE_CHECKING:
    from daemon.providers.base import Provider

logger = logging.getLogger(__name__)

_SELECTOR_SYSTEM = """\
You are a memory relevance selector. Given a user query and a list of
memory entries (filename, description, type), return up to {top_k}
filenames that will clearly be useful for responding to the query.

Only include memories you are certain will be helpful. Be selective
and discerning. If no memories are relevant, return an empty list.

Respond ONLY with a JSON array of filenames, e.g.:
["user_role.md", "feedback_testing.md"]"""


class MemorySelector:
    """LLM-based memory relevance selector.

    Args:
        provider: The provider to use for side-queries.
        config: Relevance ranking configuration.
    """

    def __init__(
        self,
        provider: Provider,
        config: MemoryRelevanceRuntimeConfig,
    ) -> None:
        self._provider = provider
        self._config = config
        self._already_surfaced: set[str] = set()

    async def select(
        self,
        query: str,
        records: list[MemoryRecord],
    ) -> list[MemoryRecord]:
        """Pick the most relevant memories for the given query.

        Sends the frontmatter manifest to the provider as a side-query
        and parses the returned JSON filename list.  Filters out entries
        already surfaced in this session.

        Args:
            query: The user's current message.
            records: All available memory records.

        Returns:
            Selected records, ordered by their position in the
            original list (preserves type-then-name sort).
        """
        # Filter already-surfaced records from the candidate list.
        candidates = [r for r in records if r.relative not in self._already_surfaced]
        if not candidates:
            return []

        # Build manifest: one line per candidate.
        manifest = "\n".join(
            f"- {r.relative}: [{r.frontmatter.type.value}] {r.frontmatter.description}"
            for r in candidates
        )
        user_text = f"Query: {query}\n\nMemories:\n{manifest}"
        system = _SELECTOR_SYSTEM.format(top_k=self._config.top_k)

        try:
            response = await self._side_query(user_text, system)
        except Exception:
            logger.exception("Memory relevance side-query failed, returning empty")
            return []

        selected_files = set(_parse_filename_list(response))

        # Match against candidates only (already-surfaced excluded).
        result = [
            r
            for r in candidates
            if _filename_of(r.relative) in selected_files or r.relative in selected_files
        ]

        # Track surfaced records by their relative path (canonical key).
        self._already_surfaced.update(r.relative for r in result)
        return result

    def reset_session(self) -> None:
        """Clear the surfaced set (e.g. on /clear or session resume)."""
        self._already_surfaced.clear()

    async def _side_query(self, user_text: str, system: str) -> str:
        """Run a single-turn, tool-free LLM call and collect the text.

        Uses a short timeout to avoid blocking the main query.
        """
        from daemon.engine.context import PromptSection

        messages = [Message.user(user_text)]
        text_parts: list[str] = []

        async with asyncio.timeout(self._config.timeout):
            async for event in self._provider.stream(
                messages=messages,
                tools=None,
                system=[PromptSection(text=system)],
            ):
                if isinstance(event, TextDelta):
                    text_parts.append(event.content)
                elif isinstance(event, StreamEnd):
                    break

        return "".join(text_parts)


def _parse_filename_list(response: str) -> list[str]:
    """Extract a list of filenames from the LLM's JSON response.

    Handles both clean JSON (``["a.md", "b.md"]``) and responses
    with surrounding prose (extracts the first ``[...]`` block).
    """
    text = response.strip()

    # Try direct parse first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(f) for f in parsed if isinstance(f, str)]
    except json.JSONDecodeError:
        pass

    # Fallback: extract first JSON array from the text.
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return [str(f) for f in parsed if isinstance(f, str)]
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse filename list from selector response: %s", text[:200])
    return []


def _filename_of(relative: str) -> str:
    """Extract filename from a relative path like ``user/role.md``."""
    return relative.rsplit("/", 1)[-1] if "/" in relative else relative
