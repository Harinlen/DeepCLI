"""Built-in tool: delete a memory file."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.memory.schema import MemoryScope, MemoryType
from daemon.memory.store import MemoryStoreError


class MemoryDeleteTool(Tool):
    """Delete a memory file (standalone or aggregate)."""

    name = "memory_delete"
    description = (
        "Delete a memory file. Returns 'deleted' if removed, "
        "'not found' if the file did not exist. Use when a memory "
        "has become wrong or outdated."
    )
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.KEYED
    max_result_chars = 500

    class Input(BaseModel):
        scope: MemoryScope = MemoryScope.GLOBAL
        type: MemoryType
        filename: str

    def concurrency_key(self, params: dict[str, Any]) -> str | None:
        """Serialize deletes targeting the same memory file within the same scope."""
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
            removed = store.delete(parsed.type, parsed.filename)
        except MemoryStoreError as exc:
            return ToolResult(output=f"memory_delete failed: {exc}", is_error=True)

        if removed:
            return ToolResult(output=f"Deleted {parsed.type.value}/{parsed.filename}")
        return ToolResult(output=f"Not found: {parsed.type.value}/{parsed.filename}")
