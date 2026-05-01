"""WebSearchTool �� web search with multi-backend fallback.

"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from kernel.orchestrator.types import ToolKind
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallResult,
    ToolInputError,
)


class WebSearchTool(Tool[dict[str, Any], dict[str, Any]]):
    """Search the web and return title + URL + snippet."""

    name: ClassVar[str] = "WebSearch"
    description_key: ClassVar[str] = "tools/web_search"
    # Fallback inline description is intentionally minimal — the real text
    # lives in ``prompts/default/tools/web_search.txt`` and is rendered
    # with ``{month_year}`` substituted at schema-build time via the
    # overridden ``get_description()`` below.
    description: ClassVar[str] = "Search the web for up-to-date information."
    kind: ClassVar[ToolKind] = ToolKind.read
    should_defer: ClassVar[bool] = True
    search_hint: ClassVar[str] = "search web query find information online"
    interrupt_behavior: ClassVar[str] = "cancel"  # type: ignore[assignment]
    max_result_size_chars: ClassVar[int] = 100_000

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 2,
                "description": "Search query.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "maximum": 25,
                "description": "Number of results to return.",
            },
        },
        "required": ["query"],
    }

    # ------------------------------------------------------------------
    # Dynamic description — injects current month/year (CC parity)
    # ------------------------------------------------------------------

    def get_description(self) -> str:
        from datetime import datetime

        if self._prompt_manager is not None and self._prompt_manager.has(self.description_key):
            return self._prompt_manager.render(
                self.description_key,
                month_year=datetime.now().strftime("%B %Y"),
            )
        return self.description

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        query = input.get("query", "")
        if not query or len(query.strip()) < 2:
            raise ToolInputError("query must be at least 2 characters")

    # ------------------------------------------------------------------
    # Permission
    # ------------------------------------------------------------------

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="web search is read-only and low-risk",
        )

    def activity_description(self, input: dict[str, Any]) -> str | None:
        query = input.get("query", "")
        short = query[:40] + ("..." if len(query) > 40 else "")
        return f'Searching "{short}"'

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def call(
        self,
        input: dict[str, Any],
        ctx: Any,
    ) -> AsyncGenerator:
        from kernel.tools.web.search_backends import search_with_fallback
        from kernel.protocol.interfaces.contracts.content_block import TextBlock

        query = input["query"]
        limit = input.get("limit", 10)
        preferred = os.getenv("MUSTANG_SEARCH_BACKEND")

        results, backend_name = await search_with_fallback(
            query,
            limit,
            preferred=preferred,
        )

        # Format output
        lines: list[str] = [
            "Note: Dates in snippets below are from the original pages, not the current date.",
            "",
        ]

        if not results:
            lines.append(f"No results for {query!r} ({backend_name}).")
        else:
            for i, hit in enumerate(results, start=1):
                lines.append(f"{i}. {hit.title}")
                lines.append(f"   {hit.url}")
                if hit.snippet:
                    lines.append(f"   {hit.snippet}")
                lines.append("")
            lines.append(f"({len(results)} results via {backend_name})")

        output_text = "\n".join(lines)

        yield ToolCallResult(
            data={
                "query": query,
                "backend": backend_name,
                "result_count": len(results),
            },
            llm_content=[TextBlock(text=output_text)],
            display=TextDisplay(text=output_text),
        )


__all__ = ["WebSearchTool"]
