"""Memory subsystem for the orchestrator.

Owns global + project memory stores, the per-turn relevance selector,
and memory index building.  The :class:`Orchestrator` delegates all
memory-related queries to this manager.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.config.schema import RuntimeConfig
    from daemon.memory.store import MemoryStore
    from daemon.providers.base import Provider

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages global and project-scoped memory for a session.

    Args:
        memory_store: Cross-project long-term memory store, or ``None``
            if memory is not configured.
        config: Runtime config (for relevance settings).
        cwd: Working directory for project memory discovery.
    """

    def __init__(
        self,
        memory_store: MemoryStore | None,
        config: RuntimeConfig,
        cwd: Path,
    ) -> None:
        self._memory_store = memory_store
        self._config = config
        self._project_memory_store: MemoryStore | None = None
        self._memory_selector: Any = None  # MemorySelector | None
        self._init_project_memory(cwd)

    # -- Public properties -------------------------------------------------

    @property
    def memory_store(self) -> MemoryStore | None:
        """Global memory store (cross-project)."""
        return self._memory_store

    @property
    def project_memory_store(self) -> MemoryStore | None:
        """Project-local memory store."""
        return self._project_memory_store

    # -- Lazy selector init ------------------------------------------------

    def ensure_selector(self, provider: Provider) -> None:
        """Lazily initialize the memory relevance selector.

        Called once per session when memory record count exceeds the
        relevance threshold.
        """
        if self._memory_selector is not None:
            return
        if self._memory_store is None:
            return
        if not self._config.memory.relevance.enabled:
            return

        from daemon.memory.relevance import MemorySelector

        self._memory_selector = MemorySelector(
            provider=provider,
            config=self._config.memory.relevance,
        )

    # -- Index building ----------------------------------------------------

    async def build_index(self, user_message: str | None = None) -> str | None:
        """Build the memory index for system prompt injection.

        Uses the relevance selector when available and the record count
        exceeds threshold.  Falls back to full index otherwise.
        Appends project memory index when available.
        """
        if self._memory_store is None:
            return None

        records = self._memory_store.records()
        cfg = self._config.memory.relevance

        # Below threshold or no user message — full global index.
        if (
            not cfg.enabled
            or len(records) <= cfg.threshold
            or user_message is None
            or self._memory_selector is None
        ):
            global_index = self._memory_store.index_text()
        else:
            # LLM side-query for top-K selection.
            try:
                selected = await self._memory_selector.select(user_message, records)
            except Exception:
                logger.warning("Memory relevance selection failed, using full index")
                selected = []

            if selected:
                from daemon.memory.index_gen import render_index

                index = render_index(selected)
                global_index = (
                    f"[Showing {len(selected)} most relevant of {len(records)} memories]\n\n{index}"
                )
            else:
                global_index = self._memory_store.index_text()

        # Append project memory index (Phase 5.7C).
        if self._project_memory_store is not None:
            project_index = self._project_memory_store.index_text()
            if project_index.strip():
                return (
                    f"## Long-term Memory (global)\n\n{global_index}\n\n"
                    f"## Project Memory\n\n{project_index}"
                )

        return global_index

    # -- Hot memory (for compaction) ---------------------------------------

    def build_hot_memory_suffix(self) -> str:
        """Build text block with hot memory bodies for post-compact injection.

        Returns:
            Formatted hot memory text, or empty string if not applicable.
        """
        if self._memory_store is None:
            return ""
        if not self._config.memory.hot_cache.enabled:
            return ""

        top_n = self._config.memory.hot_cache.top_n
        hot = self._memory_store.hot_memories(top_n)
        if not hot:
            return ""

        lines = ["[Hot memories — frequently referenced facts]"]
        for rec in hot:
            try:
                _fm, body = self._memory_store.read(rec.relative)
            except Exception:
                continue
            if body.strip():
                lines.append(f"### {rec.frontmatter.name}")
                lines.append(body.strip())
                lines.append("")

        return "\n".join(lines) if len(lines) > 1 else ""

    # -- Internal ----------------------------------------------------------

    def _init_project_memory(self, cwd: Path) -> None:
        """Discover and load project-local memory if it exists."""
        project_memory_dir = cwd / ".mustang" / "memory"
        if project_memory_dir.is_dir():
            from daemon.memory.schema import PROJECT_TYPES, MemoryScope
            from daemon.memory.store import MemoryStore

            self._project_memory_store = MemoryStore(
                root=project_memory_dir,
                scope=MemoryScope.PROJECT,
                allowed_types=PROJECT_TYPES,
            )
            self._project_memory_store.load()
            logger.info("Loaded project memory from %s", project_memory_dir)
