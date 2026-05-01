"""Built-in tool: create or overwrite a memory file.

Writes a standalone or aggregate memory entry under the configured
memory root.  On each call the MemoryStore atomically rewrites the
file, refreshes its RAM cache, regenerates ``index.md``, and appends
a ``WRITE`` / ``UPDATE`` line to ``log.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.memory.schema import MemoryFrontmatter, MemoryKind, MemoryScope, MemoryType
from daemon.memory.store import MemoryStoreError


class MemoryWriteTool(Tool):
    """Create or overwrite a memory file (standalone or aggregate).

    Memory is for **cross-project long-term** facts — user identity,
    preferences, cross-project feedback, external references.  Don't
    write project-specific or single-conversation state here.  Prefer
    merging into an existing entry; only create a new file when no
    existing entry is a reasonable home for the fact.
    """

    name = "memory_write"
    description = (
        "Create or overwrite a cross-project memory file. Pass the COMPLETE "
        "file contents: type, filename (e.g. 'role.md'), name, description, "
        "kind (standalone or aggregate), and body. Description must be a "
        "concrete fact, not a category label — it appears verbatim in the "
        "memory index and is what the LLM sees next session. Before calling, "
        "scan the memory index for an existing entry you can merge into."
    )
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.KEYED
    max_result_chars = 1_000

    class Input(BaseModel):
        scope: MemoryScope = Field(
            default=MemoryScope.GLOBAL,
            description="global (cross-project, default) or project (project-local).",
        )
        type: MemoryType = Field(
            description="user | feedback | project | reference | task | context"
        )
        filename: str = Field(
            description="Filename (no path separators), must end with .md",
            min_length=1,
        )
        name: str = Field(min_length=1, max_length=100)
        description: str = Field(
            min_length=1,
            max_length=300,
            description=("Concrete fact shown verbatim in the memory index. Not a category label."),
        )
        kind: MemoryKind = Field(
            default=MemoryKind.STANDALONE,
            description="standalone (Why/How) or aggregate (bullet list).",
        )
        body: str = Field(
            max_length=20_000,
            description=(
                "Full markdown body (no frontmatter — that's built from the other fields)."
            ),
        )

    def concurrency_key(self, params: dict[str, Any]) -> str | None:
        """Serialize writes to the same memory file within the same scope."""
        filename = params.get("filename")
        if filename is None:
            return None
        scope = params.get("scope", "project")
        return f"{scope}:{filename}"

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        parsed = self.Input.model_validate(params)
        store = self._resolve_store(parsed.scope, ctx)
        if store is None:
            scope_label = parsed.scope.value
            return ToolResult(
                output=f"{scope_label.title()} memory store not available in this session.",
                is_error=True,
            )

        fm = MemoryFrontmatter(
            name=parsed.name,
            description=parsed.description,
            type=parsed.type,
            kind=parsed.kind,
        )
        try:
            path = store.write(parsed.type, parsed.filename, fm, parsed.body)
        except MemoryStoreError as exc:
            return ToolResult(output=f"memory_write failed: {exc}", is_error=True)

        return ToolResult(output=f"Wrote memory to {path}")

    @staticmethod
    def _resolve_store(scope: MemoryScope, ctx: ToolContext) -> Any:
        """Return the memory store for the given scope."""
        if scope == MemoryScope.PROJECT:
            return ctx.project_memory_store
        return ctx.memory_store
