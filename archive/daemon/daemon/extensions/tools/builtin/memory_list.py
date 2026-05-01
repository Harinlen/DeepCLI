"""Built-in tool: list memory records as structured JSON."""

from __future__ import annotations

import json
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


class MemoryListTool(Tool):
    """Return structured list of memory records.

    Output is a JSON array of
    ``{relative, name, description, type, kind}`` objects.  Use this
    to filter/navigate memory without parsing markdown index lines.
    """

    name = "memory_list"
    description = (
        "List all memory records as structured JSON. Optional 'type' "
        "filter narrows to one of user/feedback/project/reference. "
        "Returns filename, name, description, type, kind per entry."
    )
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.PARALLEL
    max_result_chars = 20_000

    class Input(BaseModel):
        scope: MemoryScope | None = None
        type: MemoryType | None = None

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        parsed = self.Input.model_validate(params)

        def _format_records(store: Any, scope_label: str) -> list[dict[str, Any]]:
            records = store.records(type_filter=parsed.type)
            return [
                {
                    "scope": scope_label,
                    "relative": r.relative,
                    "name": r.frontmatter.name,
                    "description": r.frontmatter.description,
                    "type": r.frontmatter.type.value,
                    "kind": r.frontmatter.kind.value,
                }
                for r in records
            ]

        out: list[dict[str, Any]] = []

        # Global scope.
        if parsed.scope in (None, MemoryScope.GLOBAL) and ctx.memory_store is not None:
            out.extend(_format_records(ctx.memory_store, "global"))

        # Project scope.
        if parsed.scope in (None, MemoryScope.PROJECT) and ctx.project_memory_store is not None:
            out.extend(_format_records(ctx.project_memory_store, "project"))

        if not out and ctx.memory_store is None:
            return ToolResult(
                output="Memory store not available in this session.",
                is_error=True,
            )

        return ToolResult(output=json.dumps(out, ensure_ascii=False, indent=2))
