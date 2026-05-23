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

from kernel.core.paths import user_path
from kernel.core.lifecycle import Subsystem

from . import store
from .background import BackgroundMemoryAgent
from .declarations import (
    MemoryDeclarationImportReport,
    MemoryDeclarationRecord,
    MemoryDeclarationStore,
)
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
        self._declaration_record: MemoryDeclarationRecord | None = None
        self._declaration_import_report: MemoryDeclarationImportReport | None = None

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
            from kernel.agents.mustang.llm import LLMManager

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
        resource_home = self._resource_home()
        self._global_root = resource_home / "memory" if resource_home is not None else user_path("memory")
        store.ensure_directory_tree(self._global_root)
        self._load_resource_store_declaration(resource_home)

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
            from kernel.agents.mustang.tools import ToolManager

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

    def list_records(self, category: str | None = None) -> list[dict[str, Any]]:
        """Return memory metadata for slash-command management."""
        self._index.invalidate()
        headers = (
            self._index.get_headers_by_category(category)
            if category
            else self._index.get_all_headers()
        )
        return [
            {
                "name": header.name,
                "filename": header.filename,
                "category": header.category,
                "source": header.source,
                "scope": header.scope,
                "description": header.description,
                "access_count": header.access_count,
                "age_days": header.age_days,
                "locked": header.locked,
                "rel_path": header.rel_path,
            }
            for header in sorted(headers, key=lambda h: (h.category, h.name))
        ]

    def read_record(self, name: str) -> dict[str, Any]:
        """Return one memory entry by name or filename."""
        self._index.invalidate()
        header = self._index.get_header(name)
        if header is None:
            raise KeyError(f"memory not found: {name}")
        root = self._root_for_scope(header.scope)
        if root is None:
            raise KeyError(f"memory scope unavailable: {header.scope}")
        entry = store.read_memory(root / header.rel_path)
        return {
            "name": entry.header.name,
            "filename": entry.header.filename,
            "category": entry.header.category,
            "source": entry.header.source,
            "scope": header.scope,
            "description": entry.header.description,
            "content": entry.content,
            "rel_path": header.rel_path,
        }

    def delete_record(self, name: str, *, confirm: bool = False) -> dict[str, Any]:
        """Delete one memory entry by name or filename."""
        if not confirm:
            raise PermissionError("memory delete requires --confirm")
        self._index.invalidate()
        header = self._index.get_header(name)
        if header is None:
            raise KeyError(f"memory not found: {name}")
        root = self._root_for_scope(header.scope)
        if root is None:
            raise KeyError(f"memory scope unavailable: {header.scope}")
        store.delete_memory(root, header.category, header.filename)
        store.write_log(root, "memory_delete", header.filename)
        self._index.invalidate()
        return {"name": header.name, "filename": header.filename, "deleted": True}

    def _root_for_scope(self, scope: str) -> Path | None:
        if scope == "project":
            return self._project_root
        return self._global_root

    def get_strategy_text(self) -> str:
        """Return memory usage strategy text for Channel C."""
        return self._strategy_text

    @property
    def declaration_record(self) -> MemoryDeclarationRecord | None:
        """Current ResourceStore-backed memory declaration, when enabled."""
        return self._declaration_record

    @property
    def declaration_import_report(self) -> MemoryDeclarationImportReport | None:
        """Last legacy memory declaration import report, if ResourceStore-backed."""
        return self._declaration_import_report

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

    def _load_resource_store_declaration(self, resource_home: Path | None) -> None:
        if resource_home is None or self._global_root is None:
            return
        declaration_store = MemoryDeclarationStore.open(resource_home)
        try:
            record = declaration_store.read_global()
            if record is None:
                self._declaration_import_report = declaration_store.import_legacy_config(
                    self._global_root,
                    actor="system",
                )
                record = declaration_store.read_global()
                if record is None:
                    record = declaration_store.ensure_default_global(actor="system")
            else:
                self._declaration_import_report = declaration_store.import_legacy_config(
                    self._global_root,
                    actor="system",
                    dry_run=True,
                )
            self._declaration_record = record
        finally:
            declaration_store.close()

    def _resource_home(self) -> Path | None:
        state_dir = getattr(self._module_table, "state_dir", None)
        if isinstance(state_dir, Path):
            return state_dir.parent if state_dir.name == "state" else state_dir
        return None
