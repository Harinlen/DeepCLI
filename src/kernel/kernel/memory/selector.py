"""Relevance selection: BM25 pre-filter + LLM scoring + ranking.

Default strategy: BM25 (jieba CJK) → sufficiency check → LLM scoring
→ ranking formula → hot/warm/cold.  When an embedding model is
configured, upgrades to embedding hybrid with MMR re-ranking.
"""

from __future__ import annotations

import orjson
import logging
import math
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from .llm_text import collect_llm_text
from .index import MemoryIndex
from .types import (
    CATEGORIES,
    SOURCE_WEIGHTS,
    MemoryHeader,
    ScoredMemory,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BM25 implementation (with jieba CJK tokenization)
# ---------------------------------------------------------------------------

# Try to import jieba; fall back to whitespace split
try:
    import jieba  # type: ignore[import-untyped]

    def _tokenize(text: str) -> list[str]:
        """Tokenize with jieba for CJK + whitespace for Latin."""
        return [w for w in jieba.cut(text) if w.strip()]

except ImportError:
    logger.warning("jieba not installed — BM25 CJK tokenization disabled")

    def _tokenize(text: str) -> list[str]:  # type: ignore[misc]
        """Fallback: simple whitespace + punctuation split."""
        return re.findall(r"\w+", text.lower())


class BM25Index:
    """Lightweight BM25 index over memory descriptions.

    Uses jieba for CJK segmentation (pure Python, zero external deps).
    Scores are sigmoid-normalized (from Mem0) for fusion with LLM scores.
    """

    k1: float = 1.5
    b: float = 0.75

    def __init__(self) -> None:
        self._docs: list[tuple[MemoryHeader, list[str]]] = []
        self._avg_dl: float = 0.0
        self._df: Counter[str] = Counter()
        self._n: int = 0

    def build(self, headers: list[MemoryHeader]) -> None:
        """Build the BM25 index from memory descriptions."""
        self._docs = []
        self._df = Counter()
        total_len = 0

        for h in headers:
            tokens = _tokenize(h.description.lower())
            self._docs.append((h, tokens))
            total_len += len(tokens)
            for t in set(tokens):
                self._df[t] += 1

        self._n = len(self._docs)
        self._avg_dl = total_len / max(self._n, 1)

    def query(
        self,
        text: str,
        top_n: int = 30,
    ) -> list[tuple[MemoryHeader, float]]:
        """Return top_n candidates with sigmoid-normalized BM25 scores."""
        if not self._docs:
            return []

        q_tokens = _tokenize(text.lower())
        if not q_tokens:
            return []

        scores: list[tuple[MemoryHeader, float]] = []
        for header, doc_tokens in self._docs:
            score = self._score_doc(q_tokens, doc_tokens)
            if score > 0:
                scores.append((header, score))

        # Sort by raw score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        top = scores[:top_n]

        # Sigmoid normalize (from Mem0)
        # Adaptive midpoint/steepness based on query length
        if not top:
            return []
        max_score = top[0][1]
        midpoint = max_score * 0.3
        steepness = 2.0 / max(midpoint, 0.1)
        normalized = [(h, 1.0 / (1.0 + math.exp(-steepness * (s - midpoint)))) for h, s in top]
        return normalized

    def _score_doc(self, q_tokens: list[str], doc_tokens: list[str]) -> float:
        """Compute BM25 score for a single document."""
        if not doc_tokens:
            return 0.0
        dl = len(doc_tokens)
        tf_map: Counter[str] = Counter(doc_tokens)
        score = 0.0
        for qt in q_tokens:
            if qt not in self._df:
                continue
            tf = tf_map.get(qt, 0)
            if tf == 0:
                continue
            idf = math.log((self._n - self._df[qt] + 0.5) / (self._df[qt] + 0.5) + 1.0)
            tf_norm = (tf * (self.k1 + 1)) / (
                tf + self.k1 * (1 - self.b + self.b * dl / self._avg_dl)
            )
            score += idf * tf_norm
        return score


# ---------------------------------------------------------------------------
# Manifest builder (for LLM scoring prompt)
# ---------------------------------------------------------------------------


def build_manifest(
    candidates: list[MemoryHeader],
) -> tuple[str, dict[int, MemoryHeader]]:
    """Build the scored manifest text + alias→header mapping.

    Each candidate gets a short integer alias [0],[1],[2]... (from Mem0)
    to prevent LLM hallucination on long filenames.
    """
    alias_map: dict[int, MemoryHeader] = {}
    by_cat: dict[str, list[tuple[int, MemoryHeader]]] = {}

    for i, h in enumerate(candidates):
        alias_map[i] = h
        by_cat.setdefault(h.category, []).append((i, h))

    lines: list[str] = []
    for cat in CATEGORIES:
        entries = by_cat.get(cat, [])
        if not entries:
            continue
        lines.append(f"## {cat}")
        for alias, h in entries:
            age = h.age_days
            age_str = f"{age}d ago" if age > 0 else "today"
            # Include full description (200-500 tokens, L1 level)
            desc = h.description.strip().replace("\n", " | ")
            lines.append(f"- [{alias}] ({age_str}): {desc}")
        lines.append("")

    return "\n".join(lines), alias_map


# ---------------------------------------------------------------------------
# RelevanceSelector
# ---------------------------------------------------------------------------


class RelevanceSelector:
    """BM25 pre-filter + LLM scoring with structured output.

    Default strategy (no embedding model required):
    1. Build BM25 index from all memory descriptions
    2. BM25 pre-filter → top 30 candidates
    3. Sufficiency check (no LLM — pure rule)
    4. LLM scoring with alias-mapped manifest
    5. Ranking: llm_relevance × salience × time_decay × source_weight
    6. Filter by threshold, return top_n
    """

    SCORE_THRESHOLD = 2

    def __init__(
        self,
        memory_index: MemoryIndex,
        llm_provider: Any = None,
        memory_model: str | None = None,
        prompt_path: Path | None = None,
    ) -> None:
        self._index = memory_index
        self._llm = llm_provider
        self._model = memory_model
        self._bm25 = BM25Index()
        self._prompt_template = ""
        if prompt_path and prompt_path.exists():
            self._prompt_template = prompt_path.read_text(encoding="utf-8")

    def rebuild_bm25(self) -> None:
        """Rebuild BM25 index from current MemoryIndex headers."""
        headers = self._index.get_all_headers()
        self._bm25.build(headers)

    async def select(
        self,
        query: str,
        *,
        top_n: int = 5,
    ) -> list[ScoredMemory]:
        """Select top-N relevant memories for a query.

        Returns scored memories sorted by final_score descending.
        """
        all_headers = self._index.get_all_headers()
        if not all_headers:
            return []

        # Rebuild BM25 if needed (lazy)
        if not self._bm25._docs:
            self._bm25.build(all_headers)

        # Step 2: BM25 pre-filter
        bm25_results = self._bm25.query(query, top_n=30)
        candidates = [h for h, _ in bm25_results]

        # Supplement with hot memories that BM25 might miss
        if len(candidates) < 30:
            hot_memories = self._index.get_headers_by_hotness("hot")
            existing = {h.filename for h in candidates}
            for h in hot_memories:
                if h.filename not in existing and len(candidates) < 30:
                    candidates.append(h)

        # Step 3: Sufficiency check (pure rule, no LLM)
        if not candidates:
            return []

        # Step 4: LLM scoring
        scored = await self._llm_score(query, candidates)

        # Step 5: Ranking formula
        ranked = self._rank(scored)

        # Step 6: Filter by threshold
        filtered = [s for s in ranked if s.relevance >= self.SCORE_THRESHOLD]

        # Step 7: Top N
        result = filtered[:top_n]

        # Step 8: Update access_count for selected memories
        # (will be persisted by the caller via store.write_memory)
        for sm in result:
            # Mutate via replace since MemoryHeader is frozen
            new_header = replace(
                sm.header,
                access_count=sm.header.access_count + 1,
                updated=sm.header.updated,  # don't change updated on access
            )
            sm.header = new_header  # type: ignore[misc]

        return result

    async def _llm_score(
        self,
        query: str,
        candidates: list[MemoryHeader],
    ) -> list[ScoredMemory]:
        """Use LLM to score candidates. Falls back to BM25-only if no LLM."""
        manifest_text, alias_map = build_manifest(candidates)

        if self._llm is None:
            # No LLM available — use BM25 rank as relevance proxy
            return [
                ScoredMemory(
                    header=h,
                    relevance=3,  # neutral score
                    reason="BM25 match (no LLM scoring available)",
                    final_score=0.0,
                )
                for h in candidates
            ]

        # Build the scoring prompt
        prompt = self._prompt_template or _DEFAULT_SELECTION_PROMPT
        prompt = prompt.replace("{{MANIFEST}}", manifest_text)
        prompt = prompt.replace("{{QUERY}}", query)
        prompt = prompt.replace("{{TOP_N}}", str(min(len(candidates), 10)))

        try:
            # Call LLM
            response = await self._call_llm(prompt)
            scored = self._parse_llm_response(response, alias_map)
            return scored
        except Exception:
            logger.warning("LLM scoring failed — falling back to BM25 only", exc_info=True)
            return [
                ScoredMemory(
                    header=h,
                    relevance=3,
                    reason="BM25 match (LLM scoring failed)",
                    final_score=0.0,
                )
                for h in candidates
            ]

    async def _call_llm(self, prompt: str) -> str:
        """Call the LLM provider for scoring. Subclasses can override."""
        if self._llm is None:
            return "[]"
        return await collect_llm_text(
            self._llm,
            model=self._model,
            prompt=prompt,
            system_text="Score DeepCLI memory entries for relevance. Return only valid JSON.",
            temperature=0.0,
            max_tokens=2000,
        )

    def _parse_llm_response(
        self,
        response: str,
        alias_map: dict[int, MemoryHeader],
    ) -> list[ScoredMemory]:
        """Parse LLM JSON response into ScoredMemory list."""
        # Extract JSON array from response (may have markdown fences)
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        if not json_match:
            logger.warning("No JSON array found in LLM scoring response")
            return []

        try:
            items = orjson.loads(json_match.group())
        except orjson.JSONDecodeError:
            logger.warning("Failed to parse LLM scoring JSON")
            return []

        scored: list[ScoredMemory] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            if not isinstance(alias, int) or alias not in alias_map:
                continue
            relevance = item.get("relevance", 3)
            if not isinstance(relevance, int):
                relevance = 3
            relevance = max(1, min(5, relevance))
            reason = item.get("reason", "")

            scored.append(
                ScoredMemory(
                    header=alias_map[alias],
                    relevance=relevance,
                    reason=str(reason),
                    final_score=0.0,
                )
            )
        return scored

    def _rank(self, scored: list[ScoredMemory]) -> list[ScoredMemory]:
        """Apply the ranking formula to all scored memories.

        final_score = llm_relevance * salience * time_decay * source_weight

        All factors from validated benchmarks:
        - salience = log(access_count + 2)  (MemU, +2 cold-start fix)
        - time_decay = 1.0 if evergreen else exp(-0.693 * age/30)  (MemU + OpenClaw)
        - source_weight = {user: 1.0, agent: 0.8, extracted: 0.6}  (Second-Me)
        """
        for sm in scored:
            h = sm.header
            salience = math.log(h.access_count + 2)
            time_decay = 1.0 if h.evergreen else math.exp(-0.693 * h.age_days / 30)
            source_weight = SOURCE_WEIGHTS.get(h.source, 0.6)
            sm.final_score = sm.relevance * salience * time_decay * source_weight

        scored.sort(key=lambda s: s.final_score, reverse=True)
        return scored


# ---------------------------------------------------------------------------
# Default selection prompt
# ---------------------------------------------------------------------------

_DEFAULT_SELECTION_PROMPT = """You are selecting memories relevant to a user's query.

Given the following memory manifest organized by category, select the most relevant memories.

## Memory Manifest
{{MANIFEST}}

## User Query
{{QUERY}}

## Instructions
- Select up to {{TOP_N}} most relevant memories
- Return a JSON array: [{"alias": <int>, "relevance": <1-5>, "reason": "<one line>"}]
- relevance 5 = critical, 1 = barely relevant
- Newer memories preferred when relevance is similar
- Ensure topic diversity across categories
- Profile memories are almost always relevant for personalization queries
- If two memories contradict, note it in the reason
- Return ONLY the JSON array, no other text
"""
