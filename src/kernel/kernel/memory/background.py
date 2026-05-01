"""Background Memory Agent — extraction, consolidation, and hygiene.

Runs as a single lightweight async task (not a dual-agent architecture).
Uses the memory_model (optionally cheaper LLM) for all operations.

Three-layer extraction with mutual exclusion:
  Layer 1: Main agent direct write (detected → skip layers 2/3)
  Layer 2: Pre-compaction flush (context about to be lost)
  Layer 3: Periodic consolidation (dedup, contradict, hotness, history)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .llm_text import collect_llm_text
from . import store
from .index import MemoryIndex
from .types import (
    CATEGORIES,
    MemoryHeader,
)

logger = logging.getLogger(__name__)

# Consolidation trigger thresholds
_TURNS_BETWEEN_CONSOLIDATION = 10
_MIN_MEMORIES_FOR_DEDUP = 5


class BackgroundMemoryAgent:
    """Lightweight async agent for memory extraction and consolidation.

    Uses ``memory_model`` (optionally a cheaper LLM like Haiku) for all
    operations.  Runs as a single ``asyncio.Task`` — not a full agent
    loop.
    """

    def __init__(
        self,
        memory_index: MemoryIndex,
        global_root: Path,
        project_root: Path | None,
        llm_provider: Any = None,
        memory_model: str | None = None,
        extraction_prompt: str = "",
        consolidation_prompt: str = "",
    ) -> None:
        self._index = memory_index
        self._global_root = global_root
        self._project_root = project_root
        self._llm = llm_provider
        self._model = memory_model
        self._extraction_prompt = extraction_prompt
        self._consolidation_prompt = consolidation_prompt

        self._turn_count = 0
        self._main_agent_wrote_this_turn = False
        self._task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

    # -- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the background consolidation loop."""
        self._shutdown_event.clear()
        self._task = asyncio.create_task(self._consolidation_loop(), name="memory-background")

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the background task with timeout."""
        self._shutdown_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

    # -- Layer 1: Main agent write detection --------------------------------

    def notify_main_agent_write(self) -> None:
        """Called when the main agent writes memory via tools.

        Sets a flag so Layer 2/3 skip extraction for this turn
        (mutual exclusion — avoid duplicate extraction).
        """
        self._main_agent_wrote_this_turn = True

    def on_turn_end(self) -> None:
        """Called at end of each turn to advance state."""
        self._turn_count += 1
        self._main_agent_wrote_this_turn = False

    # -- Layer 2: Pre-compaction flush --------------------------------------

    async def on_pre_compact(self, messages: list[dict[str, Any]]) -> None:
        """Called before context compaction.

        Extracts memories from messages about to be lost.
        Skipped if main agent already wrote memory this turn.
        """
        if self._main_agent_wrote_this_turn:
            logger.debug("Skipping pre-compact flush — main agent already wrote memory")
            return

        if self._llm is None:
            logger.debug("Skipping pre-compact flush — no LLM available")
            return

        logger.info("Pre-compact flush: extracting memories from %d messages", len(messages))

        try:
            # Separate user vs assistant messages (dual prompt, from Mem0)
            user_messages = [m for m in messages if m.get("role") == "user"]
            assistant_messages = [m for m in messages if m.get("role") == "assistant"]

            # Extract from user messages (profile, preferences, facts)
            if user_messages:
                await self._extract_memories(
                    user_messages,
                    focus="user statements, preferences, identity, facts",
                )

            # Extract from assistant messages (decisions, patterns)
            if assistant_messages:
                await self._extract_memories(
                    assistant_messages,
                    focus="decisions made, patterns observed, procedures followed",
                )

            self._main_agent_wrote_this_turn = True  # prevent Layer 3 double-extraction

        except Exception:
            logger.warning("Pre-compact flush failed", exc_info=True)

    async def _extract_memories(
        self,
        messages: list[dict[str, Any]],
        focus: str,
    ) -> None:
        """Extract memories from a list of messages using LLM."""
        if not self._llm or not messages:
            return

        # Build message content for extraction
        content_parts = []
        for m in messages[:20]:  # limit to avoid token overflow
            role = m.get("role", "unknown")
            text = m.get("content", "")
            if isinstance(text, str):
                content_parts.append(f"[{role}]: {text[:1000]}")

        text_block = "\n\n".join(content_parts)

        prompt = self._extraction_prompt or _DEFAULT_EXTRACTION_PROMPT
        prompt = prompt.replace("{{MESSAGES}}", text_block)
        prompt = prompt.replace("{{FOCUS}}", focus)

        try:
            response = await self._call_llm(prompt)
            extracted = self._parse_extraction_response(response)

            for item in extracted:
                name = item.get("name", "")
                category = item.get("category", "semantic")
                description = item.get("description", "")
                content = item.get("content", "")

                if not name or not description:
                    continue

                # Sanitize
                try:
                    stem = store.sanitize_filename(name)
                except ValueError:
                    continue

                # Injection scan (hallucination filter, from MeMOS)
                if not store.scan_content(content) or not store.scan_content(description):
                    logger.info("Extracted memory '%s' rejected: injection pattern", name)
                    continue

                if category not in CATEGORIES:
                    category = "semantic"

                header = MemoryHeader(
                    filename=stem,
                    name=stem,
                    description=description,
                    category=category,  # type: ignore[arg-type]
                    source="extracted",
                    access_count=0,
                    locked=False,
                    rel_path=f"{category}/{stem}.md",
                )

                store.write_memory(
                    self._global_root,
                    category,  # type: ignore[arg-type]
                    header,
                    content,
                )
                store.write_log(self._global_root, "memory_extract", stem)

            if extracted:
                self._index.invalidate()

        except Exception:
            logger.warning("Memory extraction failed", exc_info=True)

    def _parse_extraction_response(self, response: str) -> list[dict[str, Any]]:
        """Parse LLM extraction response into memory items."""
        import re

        import orjson

        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if not json_match:
            return []
        try:
            items = orjson.loads(json_match.group())
            if isinstance(items, list):
                return [i for i in items if isinstance(i, dict)]
        except orjson.JSONDecodeError:
            pass
        return []

    # -- Layer 3: Periodic consolidation ------------------------------------

    async def _consolidation_loop(self) -> None:
        """Background loop that periodically runs consolidation."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=60.0,  # check every 60 seconds
                )
                break  # shutdown requested
            except asyncio.TimeoutError:
                pass  # normal timeout, check if consolidation needed

            if self._should_consolidate():
                await self.run_consolidation()

    def _should_consolidate(self) -> bool:
        """Check if consolidation is needed."""
        if self._turn_count < _TURNS_BETWEEN_CONSOLIDATION:
            return False
        headers = self._index.get_all_headers()
        if len(headers) < _MIN_MEMORIES_FOR_DEDUP:
            return False
        return True

    async def run_consolidation(self) -> None:
        """Run the full consolidation pipeline.

        1. Dedup/merge (4-type decisions: skip/create/merge/delete — from OpenViking)
        2. Hotness update (hot/warm/cold classification)
        3. Contradiction detection
        4. Profile change tracking (history.md)
        5. Index rebuild
        """
        logger.info("Running memory consolidation")

        try:
            headers = self._index.get_all_headers()

            # 1. Dedup check (using LLM if available)
            if self._llm and len(headers) >= _MIN_MEMORIES_FOR_DEDUP:
                await self._dedup_check(headers)

            # 2. Hotness is computed on-the-fly by MemoryIndex — no persistence needed

            # 3. Contradiction detection (using LLM if available)
            if self._llm and len(headers) >= 2:
                await self._contradiction_check(headers)

            # 4. Index rebuild
            self._index.invalidate()
            self._index.flush_index()

            # Reset turn counter
            self._turn_count = 0

            logger.info("Consolidation complete")

        except Exception:
            logger.warning("Consolidation failed", exc_info=True)

    async def _dedup_check(self, headers: list[MemoryHeader]) -> None:
        """Use LLM to find and merge duplicate memories."""
        if not self._llm:
            return

        # Build manifest of all descriptions for LLM
        manifest_lines = []
        for i, h in enumerate(headers):
            first_line = h.description.split("\n")[0][:100]
            manifest_lines.append(f"[{i}] {h.category}/{h.name}: {first_line}")
        manifest = "\n".join(manifest_lines)

        prompt = (
            "Given these memory entries, identify any duplicates or entries "
            "that should be merged. Return a JSON array of merge actions:\n"
            '[{"action": "merge", "keep": <index>, "remove": <index>, "reason": "..."}]\n'
            "Only include entries that are genuinely redundant. Return [] if none.\n\n"
            f"{manifest}"
        )

        try:
            response = await self._call_llm(prompt)
            actions = self._parse_extraction_response(response)
            for action in actions:
                if action.get("action") == "merge":
                    remove_idx = action.get("remove")
                    if isinstance(remove_idx, int) and 0 <= remove_idx < len(headers):
                        h = headers[remove_idx]
                        store.delete_memory(self._global_root, h.category, h.filename)
                        store.write_log(
                            self._global_root,
                            "memory_merge_delete",
                            h.filename,
                            action.get("reason", "duplicate"),
                        )
            self._index.invalidate()
        except Exception:
            logger.warning("Dedup check failed", exc_info=True)

    async def _contradiction_check(self, headers: list[MemoryHeader]) -> None:
        """Use LLM to find contradicting memories."""
        if not self._llm:
            return

        manifest_lines = []
        for i, h in enumerate(headers):
            first_line = h.description.split("\n")[0][:100]
            manifest_lines.append(f"[{i}] {h.category}/{h.name}: {first_line}")
        manifest = "\n".join(manifest_lines)

        prompt = (
            "Given these memory entries, identify any contradictions "
            "(entries that state conflicting facts). Return a JSON array:\n"
            '[{"entries": [<idx1>, <idx2>], "conflict": "description of conflict"}]\n'
            "Return [] if no contradictions found.\n\n"
            f"{manifest}"
        )

        try:
            response = await self._call_llm(prompt)
            conflicts = self._parse_extraction_response(response)
            for conflict in conflicts:
                entries = conflict.get("entries", [])
                desc = conflict.get("conflict", "")
                if len(entries) >= 2:
                    names = [
                        headers[i].name
                        for i in entries
                        if isinstance(i, int) and 0 <= i < len(headers)
                    ]
                    logger.warning(
                        "Memory contradiction detected: %s — %s",
                        ", ".join(names),
                        desc,
                    )
        except Exception:
            logger.warning("Contradiction check failed", exc_info=True)

    # -- LLM helper ---------------------------------------------------------

    async def _call_llm(self, prompt: str) -> str:
        """Call the LLM provider for background operations."""
        if self._llm is None:
            return "[]"
        return await collect_llm_text(
            self._llm,
            model=self._model,
            prompt=prompt,
            system_text="Maintain DeepCLI memory entries. Return only valid JSON.",
            temperature=0.0,
            max_tokens=2000,
        )


# ---------------------------------------------------------------------------
# Default extraction prompt
# ---------------------------------------------------------------------------

_DEFAULT_EXTRACTION_PROMPT = """Analyze the following conversation messages and extract memories worth preserving.

Focus on: {{FOCUS}}

## Messages
{{MESSAGES}}

## Instructions
Extract important facts, preferences, events, or patterns. For each, return:
- name: lowercase filename (a-z, 0-9, hyphens, underscores)
- category: one of "profile", "semantic", "episodic", "procedural"
- description: 200-500 token summary (this is the primary retrieval target)
- content: full detail

Category guidelines:
- profile: user identity, preferences, habits
- semantic: facts about tech stack, team, domain knowledge
- episodic: specific events, decisions, incidents with dates
- procedural: workflows, patterns, coding conventions

Return a JSON array: [{"name": "...", "category": "...", "description": "...", "content": "..."}]
Return [] if nothing worth extracting.
Only extract genuinely useful long-term information — not transient task details.
"""
