"""Memory subsystem type definitions.

All shared types for the memory subsystem live here to avoid circular
imports between store, index, selector, and tools modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol

# ---------------------------------------------------------------------------
# Category & Source enums (as Literal unions for lightweight typing)
# ---------------------------------------------------------------------------

MemoryCategory = Literal["profile", "semantic", "episodic", "procedural"]
"""Cognitive-science-driven memory classification (from Hindsight)."""

MemorySource = Literal["user", "agent", "extracted"]
"""How the memory was created — determines source_weight in ranking."""

CATEGORIES: list[MemoryCategory] = ["profile", "semantic", "episodic", "procedural"]

EVERGREEN_CATEGORIES: frozenset[MemoryCategory] = frozenset({"profile", "semantic", "procedural"})
"""Categories exempt from time decay (from OpenClaw evergreen concept)."""

SOURCE_WEIGHTS: dict[MemorySource, float] = {
    "user": 1.0,
    "agent": 0.8,
    "extracted": 0.6,
}
"""From Second-Me ConfidenceLevel — write-time source credibility."""

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryHeader:
    """Parsed YAML frontmatter of a memory file.

    Loaded once by MemoryIndex and cached in memory. The ``description``
    field (200-500 tokens, L1-level summary) is the primary target for
    BM25 indexing and LLM scoring — NOT the content body.
    """

    filename: str
    """Stem name (no path, no extension), e.g. ``"identity"``."""

    name: str
    description: str
    category: MemoryCategory
    source: MemorySource
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0
    locked: bool = False

    # Derived — set by MemoryIndex after load
    scope: Literal["global", "project"] = "global"
    rel_path: str = ""
    """Relative path from memory root, e.g. ``"profile/identity.md"``."""

    @property
    def age_days(self) -> int:
        delta = datetime.now(timezone.utc) - self.updated
        return max(0, delta.days)

    @property
    def evergreen(self) -> bool:
        return self.category in EVERGREEN_CATEGORIES


@dataclass
class MemoryEntry:
    """A full memory record — header + content body.

    Returned by ``query_relevant()`` and injected into the prompt.
    """

    header: MemoryHeader
    content: str

    # Convenience proxies
    @property
    def name(self) -> str:
        return self.header.name

    @property
    def description(self) -> str:
        return self.header.description

    @property
    def category(self) -> MemoryCategory:
        return self.header.category

    @property
    def age_days(self) -> int:
        return self.header.age_days

    @property
    def access_count(self) -> int:
        return self.header.access_count

    @property
    def source(self) -> MemorySource:
        return self.header.source


@dataclass
class ScoredMemory:
    """A memory with its computed relevance score."""

    header: MemoryHeader
    relevance: int
    """LLM-assigned relevance 1-5."""
    reason: str
    """One-line explanation from LLM."""
    final_score: float
    """``llm_relevance * salience * time_decay * source_weight``."""

    content: str = ""
    """Populated when the memory is selected for injection."""


# ---------------------------------------------------------------------------
# Hotness classification (from OpenViking, thresholds 0.6 / 0.2)
# ---------------------------------------------------------------------------

Hotness = Literal["hot", "warm", "cold"]

HOT_THRESHOLD = 0.6
COLD_THRESHOLD = 0.2


def classify_hotness(score: float) -> Hotness:
    """Classify a static hotness score into hot/warm/cold."""
    if score > HOT_THRESHOLD:
        return "hot"
    if score < COLD_THRESHOLD:
        return "cold"
    return "warm"


# ---------------------------------------------------------------------------
# MemoryProvider Protocol (read-only interface for Orchestrator)
# ---------------------------------------------------------------------------


class MemoryProvider(Protocol):
    """Narrow read-only interface consumed by Orchestrator / PromptBuilder.

    ``MemoryManager`` implements this protocol. The Orchestrator only
    holds ``deps.memory: MemoryProvider | None`` — it never imports
    ``MemoryManager`` directly.
    """

    async def get_index_text(self) -> str:
        """Return ``index.md`` content for system prompt injection.

        Cacheable — only changes on write operations (invalidated by
        memory tools).
        """
        ...

    async def query_relevant(
        self,
        prompt_text: str,
        *,
        top_n: int = 5,
    ) -> list[MemoryEntry]:
        """Score and return top-N relevant memories for this prompt.

        Called once per turn by PromptBuilder (prefetch-once pattern).
        Uses ``memory_model`` for LLM scoring.
        """
        ...


# ---------------------------------------------------------------------------
# Disposition config (from Hindsight, per-project behavior tuning)
# ---------------------------------------------------------------------------


@dataclass
class DispositionConfig:
    """Per-project memory behavior configuration."""

    skepticism: int = 3
    """1-5. Higher = more verify caveats on injected memories."""

    recency_bias: int = 3
    """1-5. Higher = stronger preference for newer memories."""

    verbosity: int = 3
    """1-5. Lower = inject description only; higher = inject full content."""
