"""ToolSearch tool — search for available lazy tools by keyword.

Lazy tools are registered in the registry but their schemas are
not sent to the LLM on every round (to save tokens).  The LLM uses
this tool to discover and load schemas for lazy tools before
calling them.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)

if TYPE_CHECKING:
    from daemon.extensions.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolSearchTool(Tool):
    """Search for available tools by keyword or exact name.

    Use ``select:<name>,<name>`` to fetch exact tools, or keywords
    to search by name and description.
    """

    name = "tool_search"
    description = (
        "Search for available tools by keyword or exact name. "
        "Use 'select:<name>' for exact lookup, or keywords to search. "
        "Returns full tool schemas so you can call the tools."
    )
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.PARALLEL

    class Input(BaseModel):
        query: str = Field(
            ...,
            description=(
                "Query to find tools. Use 'select:<name>' for exact "
                "lookup, or keywords to search."
            ),
        )
        max_results: int = Field(
            default=5,
            ge=1,
            le=20,
            description="Maximum number of results to return.",
        )

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        validated = self.Input.model_validate(params)
        query = validated.query.strip()

        if query.startswith("select:"):
            # Exact lookup by name(s).
            names = [n.strip() for n in query[7:].split(",") if n.strip()]
            matches = []
            for name in names:
                defn = self._registry.get_definition(name)
                if defn is not None:
                    matches.append(defn.model_dump())
        else:
            # Keyword search.
            results = self._registry.search(query, max_results=validated.max_results)
            matches = [r.model_dump() for r in results]

        return ToolResult(
            output=json.dumps(
                {
                    "tools": matches,
                    "total_lazy": self._registry.lazy_count,
                },
                indent=2,
            )
        )
