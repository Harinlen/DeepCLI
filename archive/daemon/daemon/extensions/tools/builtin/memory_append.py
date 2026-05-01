"""Built-in tool: append a bullet to an aggregate memory file.

Aggregate files (``kind: aggregate``) are bullet lists grouped under
``## Section`` headings.  This tool appends ``- <bullet>`` to the
end of the named section; if the section doesn't exist yet, it's
created at the end of the body.

The target file MUST already exist with ``kind: aggregate`` — call
``memory_write`` first to create it.  Frontmatter ``description`` is
**not** auto-updated by append; if a new bullet makes the existing
description stale, follow up with ``memory_write`` to refresh the
whole file.
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
from daemon.memory.schema import MemoryScope, MemoryType
from daemon.memory.store import MemoryStoreError


class MemoryAppendTool(Tool):
    """Append a bullet to an aggregate memory file's section."""

    name = "memory_append"
    description = (
        "Append a single bullet to a section of an aggregate memory file. "
        "Requires the file to already exist with kind=aggregate. Creates "
        "the section if missing. Does NOT update frontmatter.description — "
        "if the new bullet makes the index description stale, follow up "
        "with memory_write to refresh the whole file."
    )
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.KEYED
    max_result_chars = 500

    class Input(BaseModel):
        scope: MemoryScope = MemoryScope.GLOBAL
        type: MemoryType
        filename: str = Field(min_length=1)
        section: str = Field(
            min_length=1,
            description="Markdown section heading (without the '## ' prefix).",
        )
        bullet: str = Field(
            min_length=1,
            max_length=500,
            description="Bullet text (without the leading '- ').",
        )

    def concurrency_key(self, params: dict[str, Any]) -> str | None:
        """Serialize appends to the same memory file within the same scope."""
        filename = params.get("filename")
        if filename is None:
            return None
        scope = params.get("scope", "project")
        return f"{scope}:{filename}"

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        parsed = self.Input.model_validate(params)
        store = (
            ctx.project_memory_store if parsed.scope == MemoryScope.PROJECT else ctx.memory_store
        )
        if store is None:
            return ToolResult(
                output=f"{parsed.scope.value.title()} memory store not available.",
                is_error=True,
            )

        try:
            store.append(parsed.type, parsed.filename, parsed.section, parsed.bullet)
        except MemoryStoreError as exc:
            return ToolResult(output=f"memory_append failed: {exc}", is_error=True)

        return ToolResult(
            output=(f"Appended to {parsed.type.value}/{parsed.filename} [{parsed.section}]")
        )
