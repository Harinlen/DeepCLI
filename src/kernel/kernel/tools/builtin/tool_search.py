"""ToolSearch — load deferred tool schemas on demand.

ToolSearchTool is the unlock mechanism for the deferred-tool layer.
Tools registered with ``should_defer=True`` are invisible to the LLM
(only listed by name in a ``<system-reminder>``).  When the LLM needs
one, it calls ToolSearch to load the full schema.  Matched tools are
promoted from *deferred* → *core*; their schemas appear in the next
LLM turn automatically.

Two query modes:
  - ``select:Name1,Name2`` — exact lookup by primary name.
  - Free-text — case-insensitive token matching against name,
    description, and ``search_hint``.

Design mirrors Claude Code's ``ToolSearchTool`` (``tools.ts``).
"""

from __future__ import annotations

import orjson
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)

if TYPE_CHECKING:
    from kernel.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolSearchTool(Tool[dict[str, Any], list[dict[str, Any]]]):
    """Load deferred tool schemas so the LLM can call them."""

    name = "ToolSearch"
    description = "Fetches full schema definitions for deferred tools so they can be called."
    description_key = "tools/tool_search"
    kind = ToolKind.think
    should_defer = False
    always_load = True
    cache = True

    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Query to find deferred tools. Use "
                    '"select:<tool_name>" for direct selection, '
                    "or keywords to search."
                ),
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "description": "Maximum number of results to return (default: 5)",
            },
        },
        "required": ["query"],
    }

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    # ------------------------------------------------------------------
    # Tool contract
    # ------------------------------------------------------------------

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="loading tool schemas is read-only",
        )

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        query = input.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolInputError("query must be a non-empty string")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        query: str = input["query"].strip()
        max_results: int = input.get("max_results", 5)
        if max_results < 1:
            max_results = 1

        # Collect deferred tools from registry.
        deferred = self._get_deferred_tools()

        if query.startswith("select:"):
            matched = self._select_match(query, deferred)
        elif query.startswith("+"):
            matched = self._plus_match(query, deferred, max_results)
        else:
            matched = self._freetext_match(query, deferred, max_results)

        # Promote matched tools so their schemas appear next turn.
        for tool in matched:
            self._registry.promote(tool.name)

        # Build response.
        if matched:
            text = self._format_functions_block(matched)
        else:
            available = sorted(t.name for t in deferred)
            if available:
                text = (
                    f"No deferred tools matched query {query!r}. "
                    f"Available deferred tools: {', '.join(available)}"
                )
            else:
                text = "No deferred tools are currently registered."

        yield ToolCallResult(
            data=[{"name": t.name, "promoted": True} for t in matched],
            llm_content=[TextBlock(text=text)],
            display=TextDisplay(text=text),
        )

    # ------------------------------------------------------------------
    # Query modes
    # ------------------------------------------------------------------

    def _get_deferred_tools(self) -> list[Tool]:
        """Collect all tools currently in the deferred layer."""
        return [tool for tool, layer in self._registry.all_tools() if layer == "deferred"]

    def _select_match(
        self,
        query: str,
        deferred: list[Tool],
    ) -> list[Tool]:
        """``select:Name1,Name2`` — exact primary-name lookup."""
        names_raw = query[len("select:") :]
        requested = {n.strip() for n in names_raw.split(",") if n.strip()}
        return [t for t in deferred if t.name in requested]

    def _plus_match(
        self,
        query: str,
        deferred: list[Tool],
        max_results: int,
    ) -> list[Tool]:
        """``+prefix terms`` — require prefix in name, rank by terms."""
        parts = query[1:].strip().split()
        if not parts:
            return []
        required = parts[0].lower()
        rank_tokens = [p.lower() for p in parts[1:]]

        candidates = [t for t in deferred if required in t.name.lower()]
        if not rank_tokens:
            return candidates[:max_results]

        scored = []
        for tool in candidates:
            corpus = self._search_corpus(tool)
            score = sum(1 for tok in rank_tokens if tok in corpus)
            scored.append((score, tool))
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:max_results]]

    def _freetext_match(
        self,
        query: str,
        deferred: list[Tool],
        max_results: int,
    ) -> list[Tool]:
        """Free-text — case-insensitive token matching."""
        tokens = [t.lower() for t in query.split() if t]
        if not tokens:
            return []

        scored: list[tuple[float, Tool]] = []
        for tool in deferred:
            corpus = self._search_corpus(tool)
            hits = sum(1 for tok in tokens if tok in corpus)
            if hits > 0:
                scored.append((hits / len(tokens), tool))

        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:max_results]]

    @staticmethod
    def _search_corpus(tool: Tool) -> str:
        """Build a lowercase search corpus from tool metadata."""
        parts = [tool.name, tool.description]
        if tool.search_hint:
            parts.append(tool.search_hint)
        for alias in tool.aliases:
            parts.append(alias)
        return " ".join(parts).lower()

    # ------------------------------------------------------------------
    # Output formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_functions_block(tools: list[Tool]) -> str:
        """Format matched tools as a ``<functions>`` block.

        Mirrors Claude Code's ToolSearch output format so the LLM sees
        the same structure it's trained on.
        """
        lines = ["<functions>"]
        for tool in tools:
            schema = tool.to_schema()
            entry = {
                "description": schema.description,
                "name": schema.name,
                "parameters": schema.input_schema,
            }
            lines.append(f"<function>{orjson.dumps(entry).decode()}</function>")
        lines.append("</functions>")
        return "\n".join(lines)
