"""Memory tools — 5 tools for LLM-driven memory management.

Tools register into ToolManager via the standard ``BUILTIN_TOOLS``
mechanism.  All writes go through MemoryStore (atomic write + scan).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.orchestrator.types import ToolKind
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallResult,
)

if TYPE_CHECKING:
    from kernel.tools.context import ToolContext

from . import store
from .index import MemoryIndex
from .selector import RelevanceSelector
from .types import CATEGORIES, MemoryCategory, MemoryHeader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state — set by MemoryManager during startup
# ---------------------------------------------------------------------------

_memory_index: MemoryIndex | None = None
_selector: RelevanceSelector | None = None
_global_root: Any = None  # Path, set at startup
_project_root: Any = None  # Path | None, set at startup


def _configure(
    index: MemoryIndex,
    selector: RelevanceSelector,
    global_root: Any,
    project_root: Any = None,
) -> None:
    """Called by MemoryManager.startup() to wire shared state."""
    global _memory_index, _selector, _global_root, _project_root
    _memory_index = index
    _selector = selector
    _global_root = global_root
    _project_root = project_root


def _resolve_root(category: str) -> Any:
    """Resolve storage root. Project scope for project-specific categories."""
    # For now, all writes go to global root.
    # Project scope is available when _project_root is set.
    return _global_root


# ---------------------------------------------------------------------------
# MemoryWriteTool
# ---------------------------------------------------------------------------


class MemoryWriteTool(Tool[dict[str, Any], dict[str, str]]):
    """Write a new memory or overwrite an existing unlocked one."""

    name: ClassVar[str] = "memory_write"
    description: ClassVar[str] = (
        "Write a memory entry. Creates a new file or overwrites an existing one (unless locked)."
    )
    kind: ClassVar[ToolKind] = ToolKind.edit

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Memory name (lowercase, hyphens/underscores, no spaces).",
            },
            "category": {
                "type": "string",
                "enum": list(CATEGORIES),
                "description": "Memory category.",
            },
            "description": {
                "type": "string",
                "description": "200-500 token summary — the primary retrieval target.",
            },
            "content": {
                "type": "string",
                "description": "Full memory content body.",
            },
        },
        "required": ["name", "category", "description", "content"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="writing to sandboxed memory directory",
        )

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        assert _memory_index is not None and _global_root is not None

        name = input["name"]
        category: MemoryCategory = input["category"]  # type: ignore[assignment]
        description = input["description"]
        content = input["content"]

        # Sanitize
        try:
            stem = store.sanitize_filename(name)
        except ValueError as e:
            yield _error(str(e))
            return

        # Injection scan
        if not store.scan_content(content) or not store.scan_content(description):
            yield _error("Content rejected: potential prompt injection detected.")
            return

        # Check locked
        existing = _memory_index.get_header(stem)
        if existing and existing.locked:
            yield _error(f"Memory '{stem}' is locked. Edit manually to modify.")
            return

        # Track profile changes for history
        is_profile_overwrite = (
            existing is not None and category == "profile" and existing.category == "profile"
        )
        old_desc = (
            existing.description if is_profile_overwrite and existing is not None else None
        )

        root = _resolve_root(category)

        # Determine source: agent (called by main agent tool)
        source = "agent"

        header = MemoryHeader(
            filename=stem,
            name=stem,
            description=description,
            category=category,
            source=source,  # type: ignore[arg-type]
            created=existing.created if existing else datetime.now(timezone.utc),
            updated=datetime.now(timezone.utc),
            access_count=existing.access_count if existing else 0,
            locked=False,
            rel_path=f"{category}/{stem}.md",
        )

        store.write_memory(root, category, header, content)
        store.write_log(root, "memory_write", stem, f"category={category}")
        _memory_index.invalidate()

        # Profile change tracking
        if is_profile_overwrite and old_desc and old_desc != description:
            store.append_history(root, f"{stem}: description changed")

        yield _ok(f"Memory '{stem}' written to {category}/.")

    def is_destructive(self, input: dict[str, Any]) -> bool:
        return False


# ---------------------------------------------------------------------------
# MemoryAppendTool
# ---------------------------------------------------------------------------


class MemoryAppendTool(Tool[dict[str, Any], dict[str, str]]):
    """Append content to an existing memory file."""

    name: ClassVar[str] = "memory_append"
    description: ClassVar[str] = "Append content to an existing memory file."
    kind: ClassVar[ToolKind] = ToolKind.edit

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Memory name to append to.",
            },
            "content": {
                "type": "string",
                "description": "Content to append.",
            },
        },
        "required": ["name", "content"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="appending to sandboxed memory file",
        )

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        assert _memory_index is not None and _global_root is not None

        name = input["name"]
        content = input["content"]

        try:
            stem = store.sanitize_filename(name)
        except ValueError as e:
            yield _error(str(e))
            return

        if not store.scan_content(content):
            yield _error("Content rejected: potential prompt injection detected.")
            return

        header = _memory_index.get_header(stem)
        if header is None:
            yield _error(f"Memory '{stem}' not found.")
            return

        root = _resolve_root(header.category)
        store.append_memory(root, header.category, stem, content)
        store.write_log(root, "memory_append", stem)
        _memory_index.invalidate()

        yield _ok(f"Content appended to '{stem}'.")

    def is_destructive(self, input: dict[str, Any]) -> bool:
        return False


# ---------------------------------------------------------------------------
# MemoryDeleteTool
# ---------------------------------------------------------------------------


class MemoryDeleteTool(Tool[dict[str, Any], dict[str, str]]):
    """Delete a memory file (requires confirmation)."""

    name: ClassVar[str] = "memory_delete"
    description: ClassVar[str] = "Delete a memory file. Requires confirmation=true."
    kind: ClassVar[ToolKind] = ToolKind.edit

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Memory name to delete.",
            },
            "confirmation": {
                "type": "boolean",
                "description": "Must be true to confirm deletion.",
            },
        },
        "required": ["name", "confirmation"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="medium",
            default_decision="ask",
            reason="deleting a memory file",
        )

    def is_destructive(self, input: dict[str, Any]) -> bool:
        return True

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        assert _memory_index is not None and _global_root is not None

        name = input["name"]
        confirmation = input.get("confirmation", False)

        if not confirmation:
            yield _error("Deletion requires confirmation=true.")
            return

        try:
            stem = store.sanitize_filename(name)
        except ValueError as e:
            yield _error(str(e))
            return

        header = _memory_index.get_header(stem)
        if header is None:
            yield _error(f"Memory '{stem}' not found.")
            return

        root = _resolve_root(header.category)
        store.delete_memory(root, header.category, stem)
        store.write_log(root, "memory_delete", stem)
        _memory_index.invalidate()

        yield _ok(f"Memory '{stem}' deleted.")


# ---------------------------------------------------------------------------
# MemoryListTool
# ---------------------------------------------------------------------------


class MemoryListTool(Tool[dict[str, Any], list[dict[str, Any]]]):
    """List all memories with metadata (no content, no LLM call)."""

    name: ClassVar[str] = "memory_list"
    description: ClassVar[str] = "List all memories grouped by category."
    kind: ClassVar[ToolKind] = ToolKind.search

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": list(CATEGORIES),
                "description": "Filter by category (optional).",
            },
        },
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="listing memory metadata is read-only",
        )

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        assert _memory_index is not None

        category = input.get("category")
        if category:
            headers = _memory_index.get_headers_by_category(category)
        else:
            headers = _memory_index.get_all_headers()

        if not headers:
            yield _ok("No memories found.")
            return

        lines: list[str] = []
        current_cat = ""
        for h in sorted(headers, key=lambda x: (x.category, x.name)):
            if h.category != current_cat:
                current_cat = h.category
                lines.append(f"\n## {current_cat}")
            hotness = _memory_index.classify(h)
            first_line = h.description.split("\n")[0][:100]
            lines.append(
                f"- {h.name} [{h.source}] "
                f"(age={h.age_days}d, access={h.access_count}, "
                f"hotness={hotness}): {first_line}"
            )

        body = "\n".join(lines)
        yield ToolCallResult(
            data={"count": len(headers)},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


# ---------------------------------------------------------------------------
# MemorySearchTool
# ---------------------------------------------------------------------------


class MemorySearchTool(Tool[dict[str, Any], list[dict[str, Any]]]):
    """Search memories by relevance (uses BM25 + LLM scoring)."""

    name: ClassVar[str] = "memory_search"
    description: ClassVar[str] = "Search memories by semantic relevance."
    kind: ClassVar[ToolKind] = ToolKind.search

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "top_n": {
                "type": "integer",
                "description": "Number of results (default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="memory search is read-only",
        )

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallResult, None]:
        assert _selector is not None

        query = input["query"]
        top_n = input.get("top_n", 5)

        results = await _selector.select(query, top_n=top_n)

        if not results:
            yield _ok("No relevant memories found.")
            return

        lines: list[str] = []
        for sm in results:
            lines.append(
                f"- [{sm.header.category}] {sm.header.name} "
                f"(score={sm.final_score:.2f}, relevance={sm.relevance}/5): "
                f"{sm.reason}"
            )
            first_desc_line = sm.header.description.split("\n")[0][:100]
            lines.append(f"  {first_desc_line}")

        body = "\n".join(lines)
        yield ToolCallResult(
            data={"count": len(results)},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


# ---------------------------------------------------------------------------
# Tool list for ToolManager registration
# ---------------------------------------------------------------------------

MEMORY_TOOLS: list[type[Tool]] = [  # type: ignore[type-arg]
    MemoryWriteTool,
    MemoryAppendTool,
    MemoryDeleteTool,
    MemoryListTool,
    MemorySearchTool,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(message: str) -> ToolCallResult:
    return ToolCallResult(
        data={"ok": True, "message": message},
        llm_content=[TextBlock(type="text", text=message)],
        display=TextDisplay(text=message),
    )


def _error(message: str) -> ToolCallResult:
    return ToolCallResult(
        data={"ok": False, "error": message},
        llm_content=[TextBlock(type="text", text=f"Error: {message}")],
        display=TextDisplay(text=f"Error: {message}"),
    )
