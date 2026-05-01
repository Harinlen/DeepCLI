"""Memory access counting and hot-memory ranking (Phase 5.7D).

Tracks how often each memory entry is read and provides a
``hot_memories()`` query that returns the top-N most accessed
records.  Persistence is JSON-based (``{root}/.access_counts.json``).

Separated from :class:`MemoryStore` because access tracking is an
orthogonal concern — it doesn't participate in the CRUD lifecycle or
index generation.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.memory.store import _CacheEntry, MemoryRecord

logger = logging.getLogger(__name__)


class AccessTracker:
    """Tracks per-entry access counts for a memory store.

    Args:
        root: Memory store root directory (for ```.access_counts.json``).
    """

    def __init__(self, root: Path) -> None:
        self._counts: dict[str, int] = {}
        self._path = root / ".access_counts.json"
        self._lock = threading.Lock()

    # -- Public API --------------------------------------------------------

    def record(self, relative: str) -> None:
        """Increment the read count for a memory entry."""
        with self._lock:
            self._counts[relative] = self._counts.get(relative, 0) + 1

    def hot_memories(
        self,
        top_n: int,
        cache: dict[str, _CacheEntry],
        root: Path,
    ) -> list[MemoryRecord]:
        """Return the N most frequently accessed memory records.

        Args:
            top_n: Maximum number of records to return.
            cache: The store's internal ``_cache`` dict.
            root: The store's root path (for building MemoryRecord).

        Returns:
            Records sorted by access count (descending).
        """
        from daemon.memory.store import MemoryRecord

        sorted_keys = sorted(
            self._counts,
            key=lambda k: self._counts[k],
            reverse=True,
        )[:top_n]
        return [
            MemoryRecord(
                relative=k,
                path=root / k,
                frontmatter=entry.frontmatter,
                size_bytes=entry.size_bytes,
            )
            for k in sorted_keys
            if (entry := cache.get(k)) is not None
        ]

    # -- Persistence -------------------------------------------------------

    def load(self) -> None:
        """Load access counts from disk (if persisted)."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._counts = {k: int(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                logger.warning("Ignoring corrupt access counts at %s", self._path)

    def save(self) -> None:
        """Persist access counts to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._counts, indent=2), encoding="utf-8")
