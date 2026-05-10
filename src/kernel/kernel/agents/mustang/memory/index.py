"""In-memory cache of all memory file headers.

Loaded once at startup, invalidated on write operations.  Computes
static hotness scores and classifies memories as hot/warm/cold
(from OpenViking thresholds).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from . import store
from .types import (
    CATEGORIES,
    SOURCE_WEIGHTS,
    Hotness,
    MemoryHeader,
    classify_hotness,
)

logger = logging.getLogger(__name__)


class MemoryIndex:
    """In-memory cache of all memory file frontmatter.

    Provides fast lookups, index text generation, and hotness
    classification without touching the filesystem on every query.
    """

    def __init__(self) -> None:
        self._headers: dict[str, MemoryHeader] = {}  # rel_path → header
        self._index_text: str = ""
        self._dirty: bool = True
        self._global_root: Path | None = None
        self._project_root: Path | None = None

    async def load(
        self,
        global_root: Path,
        project_root: Path | None = None,
    ) -> None:
        """Scan global and project memory directories, build cache."""
        self._global_root = global_root
        self._project_root = project_root
        self._rebuild()

    def _rebuild(self) -> None:
        """Rescan filesystem and rebuild all caches."""
        self._headers.clear()

        # Global scope
        if self._global_root and self._global_root.is_dir():
            for h in store.scan_headers(self._global_root):
                key = f"global/{h.rel_path}"
                self._headers[key] = h

        # Project scope
        if self._project_root and self._project_root.is_dir():
            for h in store.scan_headers(self._project_root):
                from dataclasses import replace

                h_proj = replace(h, scope="project")
                key = f"project/{h.rel_path}"
                self._headers[key] = h_proj

        # Rebuild index text
        all_headers = sorted(
            self._headers.values(),
            key=lambda h: (CATEGORIES.index(h.category), h.name),
        )
        self._index_text = store.build_index_text(all_headers)
        self._dirty = False
        logger.info(
            "MemoryIndex loaded: %d memories (global=%s, project=%s)",
            len(self._headers),
            self._global_root,
            self._project_root,
        )

    def invalidate(self) -> None:
        """Mark cache as dirty. Next access will trigger a rebuild."""
        self._dirty = True

    def _ensure_fresh(self) -> None:
        """Rebuild if dirty."""
        if self._dirty:
            self._rebuild()

    # -- Read accessors -----------------------------------------------------

    def get_index_text(self) -> str:
        """Return cached index.md content for system prompt injection."""
        self._ensure_fresh()
        return self._index_text

    def get_all_headers(self) -> list[MemoryHeader]:
        """Return all cached headers (both scopes)."""
        self._ensure_fresh()
        return list(self._headers.values())

    def get_header(self, name: str) -> MemoryHeader | None:
        """Find a header by name (searches across scopes)."""
        self._ensure_fresh()
        for h in self._headers.values():
            if h.name == name or h.filename == name:
                return h
        return None

    def get_headers_by_category(self, category: str) -> list[MemoryHeader]:
        """Return headers for a specific category."""
        self._ensure_fresh()
        return [h for h in self._headers.values() if h.category == category]

    def get_headers_by_hotness(self, hotness: Hotness) -> list[MemoryHeader]:
        """Return headers matching a hotness classification."""
        self._ensure_fresh()
        return [h for h in self._headers.values() if self.classify(h) == hotness]

    # -- Hotness computation ------------------------------------------------

    @staticmethod
    def compute_hotness(header: MemoryHeader) -> float:
        """Compute static hotness score (query-independent).

        Formula (all factors from validated benchmarks):
        - salience = log(access_count + 2)          (from MemU, +2 avoids cold-start)
        - time_decay = 1.0 if evergreen else         (from OpenClaw)
                       exp(-0.693 * age_days / 30)   (from MemU, 30-day half-life)
        - source_weight = {user: 1.0, agent: 0.8, extracted: 0.6}  (from Second-Me)

        hotness = salience * time_decay * source_weight
        """
        salience = math.log(header.access_count + 2)
        if header.evergreen:
            time_decay = 1.0
        else:
            time_decay = math.exp(-0.693 * header.age_days / 30)
        source_weight = SOURCE_WEIGHTS.get(header.source, 0.6)
        return salience * time_decay * source_weight

    @staticmethod
    def classify(header: MemoryHeader) -> Hotness:
        """Classify a memory header as hot/warm/cold."""
        score = MemoryIndex.compute_hotness(header)
        return classify_hotness(score)

    # -- Persistence --------------------------------------------------------

    def flush_index(self) -> None:
        """Write index.md to disk for both scopes."""
        self._ensure_fresh()
        if self._global_root:
            global_headers = [h for h in self._headers.values() if h.scope == "global"]
            store.write_index(self._global_root, global_headers)
        if self._project_root:
            project_headers = [h for h in self._headers.values() if h.scope == "project"]
            store.write_index(self._project_root, project_headers)
