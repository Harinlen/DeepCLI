"""Memory subsystem — long-term memory management.

Provides:
- Hierarchical storage (profile/semantic/episodic/procedural) in MD files
- BM25 + LLM scoring retrieval with ranking formula (from MemU + OpenClaw)
- Dual-channel injection (index in system prompt + per-turn relevant memories)
- Background agent for extraction and consolidation
- 5 memory tools for LLM-driven memory management

Exposes read access to Orchestrator via ``MemoryProvider`` protocol,
write access via memory tools registered in ToolManager.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kernel.subsystem import Subsystem

from . import store
from .background import BackgroundMemoryAgent
from .index import MemoryIndex
from .selector import RelevanceSelector
from .tools import MEMORY_TOOLS, _configure
from .types import MemoryEntry
from .types import MemoryProvider as MemoryProvider  # re-export

logger = logging.getLogger(__name__)

# Prompt file locations (relative to this package)
_PROMPTS_DIR = Path(__file__).parent / "prompts"


class MemoryManager(Subsystem):
    """Manages long-term memory across global and project scopes.

    Startup position 9 (after Tools, before Session).
    Failure strategy: degrade, not abort — ``deps.memory = None``
    when this subsystem fails to load.

    Implements ``MemoryProvider`` protocol for Orchestrator consumption.
    """

    def __init__(self, module_table: Any) -> None:
        super().__init__(module_table)
        self._index = MemoryIndex()
        self._selector: RelevanceSelector | None = None
        self._background: BackgroundMemoryAgent | None = None
        self._global_root: Path | None = None
        self._project_root: Path | None = None
        self._strategy_text: str = ""

    async def startup(self) -> None:
        """Initialize memory subsystem.

        1. Resolve LLM (memory_model or default)
        2. Set up directory trees (global + project)
        3. Load MemoryIndex (scan all files)
        4. Initialize selector (BM25 + LLM scoring)
        5. Configure memory tools (wire shared state)
        6. Start background agent
        """
        # 1. Resolve LLM
        llm_provider: Any = None
        memory_model: Any = None
        try:
            from kernel.llm import LLMManager

            llm_manager = self._module_table.get(LLMManager)
            llm_provider = llm_manager
            try:
                memory_model = llm_manager.model_for("memory")
            except KeyError:
                # No memory-specific model configured — use default
                try:
                    memory_model = llm_manager.model_for("default")
                except KeyError:
                    memory_model = None
        except (KeyError, ImportError):
            logger.info("LLMManager not available — memory scoring disabled")

        # 2. Directory trees
        self._global_root = Path.home() / ".mustang" / "memory"
        store.ensure_directory_tree(self._global_root)

        # Project scope: look for .mustang/memory/ in cwd or git root
        try:
            config = self._module_table.config
            project_root = getattr(config, "project_root", None)
            if project_root:
                self._project_root = Path(project_root) / ".mustang" / "memory"
                if self._project_root.exists():
                    store.ensure_directory_tree(self._project_root)
                else:
                    self._project_root = None
        except (KeyError, ImportError):
            pass

        # 3. Load index
        await self._index.load(self._global_root, self._project_root)

        # 4. Initialize selector
        selection_prompt = _PROMPTS_DIR / "selection.txt"
        self._selector = RelevanceSelector(
            memory_index=self._index,
            llm_provider=llm_provider,
            memory_model=memory_model,
            prompt_path=selection_prompt,
        )
        self._selector.rebuild_bm25()

        # 5. Configure tools
        _configure(
            index=self._index,
            selector=self._selector,
            global_root=self._global_root,
            project_root=self._project_root,
        )

        # Register memory tools with ToolManager
        try:
            from kernel.tools import ToolManager

            tool_manager = self._module_table.get(ToolManager)
            for tool_cls in MEMORY_TOOLS:
                try:
                    tool = tool_cls()
                    tool_manager.register_tool(tool)
                except ValueError:
                    logger.debug("Memory tool %s already registered", tool_cls.name)
        except (KeyError, ImportError):
            logger.info("ToolManager not available — memory tools not registered")

        # 6. Load strategy text for Channel C
        strategy_path = _PROMPTS_DIR / "memory_strategy.txt"
        if strategy_path.exists():
            self._strategy_text = strategy_path.read_text(encoding="utf-8")

        # 7. Start background agent
        extraction_prompt = ""
        consolidation_prompt = ""
        ep = _PROMPTS_DIR / "extraction.txt"
        cp = _PROMPTS_DIR / "consolidation.txt"
        if ep.exists():
            extraction_prompt = ep.read_text(encoding="utf-8")
        if cp.exists():
            consolidation_prompt = cp.read_text(encoding="utf-8")

        self._background = BackgroundMemoryAgent(
            memory_index=self._index,
            global_root=self._global_root,
            project_root=self._project_root,
            llm_provider=llm_provider,
            memory_model=memory_model,
            extraction_prompt=extraction_prompt,
            consolidation_prompt=consolidation_prompt,
        )
        self._background.start()

        logger.info(
            "MemoryManager started: global=%s, project=%s, model=%s",
            self._global_root,
            self._project_root,
            memory_model or "default",
        )

    async def shutdown(self) -> None:
        """Shutdown memory subsystem.

        1. Stop background agent (with 5s timeout)
        2. Flush dirty index to disk
        3. Write final audit log entry
        """
        # 1. Stop background
        if self._background:
            await self._background.stop(timeout=5.0)

        # 2. Flush index
        self._index.flush_index()

        # 3. Audit log
        if self._global_root:
            store.write_log(self._global_root, "SHUTDOWN", "MemoryManager")

        logger.info("MemoryManager shutdown complete")

    # -- MemoryProvider protocol --------------------------------------------

    async def get_index_text(self) -> str:
        """Return index.md content for system prompt (Channel A, cacheable)."""
        return self._index.get_index_text()

    async def query_relevant(
        self,
        prompt_text: str,
        *,
        top_n: int = 5,
    ) -> list[MemoryEntry]:
        """Score and return top-N relevant memories (Channel B, per-turn).

        Called once per turn by PromptBuilder (prefetch-once pattern).
        """
        if self._selector is None:
            return []

        scored = await self._selector.select(prompt_text, top_n=top_n)

        # Load full content for selected memories
        entries: list[MemoryEntry] = []
        for sm in scored:
            root = self._global_root if sm.header.scope == "global" else self._project_root
            if root is None:
                continue
            path = root / sm.header.rel_path
            if path.exists():
                try:
                    entry = store.read_memory(path)
                    entries.append(entry)
                except Exception:
                    logger.warning("Failed to read memory: %s", path)

        return entries

    def get_strategy_text(self) -> str:
        """Return memory usage strategy text for Channel C."""
        return self._strategy_text

    # -- Background agent proxies -------------------------------------------

    def notify_main_agent_write(self) -> None:
        """Notify that the main agent wrote memory this turn."""
        if self._background:
            self._background.notify_main_agent_write()

    async def on_pre_compact(self, messages: list[dict[str, Any]]) -> None:
        """Called before context compaction (Layer 2)."""
        if self._background:
            await self._background.on_pre_compact(messages)

    def on_turn_end(self) -> None:
        """Called at end of each turn."""
        if self._background:
            self._background.on_turn_end()
