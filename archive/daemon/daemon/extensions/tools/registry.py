"""Tool registry — name → Tool mapping with core/lazy split.

Provides tool lookup for the orchestrator and ``get_definitions()``
to generate the ``tools`` parameter sent to LLM providers.

Core tools have their schemas sent every round.  Lazy tools are
registered but their schemas are only sent when the LLM uses
``tool_search`` to look them up.  This saves tokens as the tool set
grows (MCP tools, user tools, etc.).

Note: "lazy" is independent from a tool's ``defer_execution`` flag,
which controls whether the orchestrator pauses execution to ask the
user for approval before running.  A tool can be lazy or eager, and
independently can defer execution or not.
"""

from __future__ import annotations

import logging

from daemon.engine.stream import ToolDefinition
from daemon.extensions.tools.base import Tool, ToolDescriptionContext

logger = logging.getLogger(__name__)

# Tools whose schemas are sent on every LLM round.
CORE_TOOL_NAMES: frozenset[str] = frozenset({
    "file_read",
    "file_edit",
    "file_write",
    "bash",
    "glob",
    "grep",
    "agent",
    "skill",
    "todo_write",
    "tool_search",
    "http_fetch",
    # "page_fetch",  # disabled
    "web_search",
    "enter_plan_mode",
    "exit_plan_mode",
})


class ToolRegistry:
    """Registry of available tools with core/lazy split.

    Tools are registered by name.  The orchestrator looks up tools
    by name when the LLM emits a ``tool_use`` event, and calls
    ``get_core_definitions()`` to build the ``tools`` parameter for
    the provider (only core tool schemas).

    Lazy tools are still callable — the LLM just doesn't see
    their schemas unless it uses ``tool_search`` first.
    """

    def __init__(self) -> None:
        self._core_tools: dict[str, Tool] = {}
        self._lazy_tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool instance (auto-classified as core or lazy).

        Args:
            tool: Tool to register.  Its ``name`` must be unique.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if tool.name in self._core_tools or tool.name in self._lazy_tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        if tool.name in CORE_TOOL_NAMES:
            self._core_tools[tool.name] = tool
        else:
            self._lazy_tools[tool.name] = tool
        logger.debug("Registered tool: %s (core=%s)", tool.name, tool.name in CORE_TOOL_NAMES)

    def unregister(self, name: str) -> bool:
        """Remove a tool by name.

        Used by the MCP bridge when refreshing tools after reconnect.
        """
        if name in self._core_tools:
            del self._core_tools[name]
            logger.debug("Unregistered tool: %s", name)
            return True
        if name in self._lazy_tools:
            del self._lazy_tools[name]
            logger.debug("Unregistered tool: %s", name)
            return True
        return False

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name (searches both core and lazy)."""
        return self._core_tools.get(name) or self._lazy_tools.get(name)

    def _build_context(self) -> ToolDescriptionContext:
        """Build shared description context."""
        all_names = frozenset(self._core_tools.keys() | self._lazy_tools.keys())
        has_mcp = any(n.startswith("mcp__") for n in all_names)
        return ToolDescriptionContext(
            registered_tool_names=all_names,
            has_mcp_tools=has_mcp,
        )

    def get_core_definitions(self) -> list[ToolDefinition]:
        """Build definitions for core tools only (sent every round).

        Returns:
            List of ``ToolDefinition`` for core tools.
        """
        ctx = self._build_context()
        return [
            ToolDefinition(
                name=tool.name,
                description=tool.get_description(ctx),
                parameters=tool.input_schema(),
            )
            for tool in self._core_tools.values()
        ]

    def get_definitions(self) -> list[ToolDefinition]:
        """Build definitions for ALL tools (core + lazy).

        Used by sub-agent clone and backward-compat callers.
        """
        ctx = self._build_context()
        all_tools = list(self._core_tools.values()) + list(self._lazy_tools.values())
        return [
            ToolDefinition(
                name=tool.name,
                description=tool.get_description(ctx),
                parameters=tool.input_schema(),
            )
            for tool in all_tools
        ]

    def get_definition(self, name: str) -> ToolDefinition | None:
        """Look up a single tool's definition by name."""
        tool = self.get(name)
        if tool is None:
            return None
        ctx = self._build_context()
        return ToolDefinition(
            name=tool.name,
            description=tool.get_description(ctx),
            parameters=tool.input_schema(),
        )

    def search(self, query: str, max_results: int = 5) -> list[ToolDefinition]:
        """Search lazy tools by keyword (name + description).

        Supports both whole-string and per-token matching so queries
        like ``"web search Sydney weather"`` still find ``web_search``.

        Scoring tiers (highest wins):
          5 — exact name match
          4 — name prefix match
          3 — whole query is a substring of name
          2 — every query token appears in name (tokenised)
          1 — every query token appears in name OR description

        Args:
            query: Case-insensitive search string.
            max_results: Maximum results to return.

        Returns:
            Matching tool definitions, best matches first.
        """
        q = query.lower()
        # Tokenise: split on whitespace/underscores, drop very short noise.
        tokens = [t for t in q.replace("_", " ").split() if len(t) >= 2]
        ctx = self._build_context()
        scored: list[tuple[float, ToolDefinition]] = []

        for tool in self._lazy_tools.values():
            name_lower = tool.name.lower()
            # Normalise underscores so "web_search" matches tokens ["web", "search"].
            name_normalised = name_lower.replace("_", " ")
            desc_lower = tool.get_description(ctx).lower()
            haystack = name_normalised + " " + desc_lower

            # Tier 1: whole-string matches (high confidence).
            if name_lower == q or name_normalised == q:
                score = 100.0
            elif name_lower.startswith(q) or name_normalised.startswith(q):
                score = 90.0
            elif q in name_lower or q in name_normalised:
                score = 80.0
            elif tokens:
                # Tier 2: token overlap scoring.
                # Count how many query tokens appear in the tool's name or description.
                name_hits = sum(1 for t in tokens if t in name_normalised)
                desc_hits = sum(1 for t in tokens if t in desc_lower)
                total_hits = sum(1 for t in tokens if t in haystack)

                if total_hits == 0:
                    continue

                # Name hits are worth more.  Score = weighted hit ratio.
                # name hit = 2 points, desc-only hit = 1 point, max = 2 * len(tokens).
                raw = name_hits * 2 + (total_hits - name_hits)
                score = raw / (2 * len(tokens)) * 70  # scale to 0-70 range
            else:
                continue

            scored.append((
                score,
                ToolDefinition(
                    name=tool.name,
                    description=tool.get_description(ctx),
                    parameters=tool.input_schema(),
                ),
            ))

        scored.sort(key=lambda x: -x[0])
        return [td for _, td in scored[:max_results]]

    @property
    def lazy_tool_names(self) -> list[str]:
        """Sorted list of lazy tool names (for system prompt)."""
        return sorted(self._lazy_tools.keys())

    @property
    def lazy_count(self) -> int:
        """Number of lazy tools."""
        return len(self._lazy_tools)

    @property
    def tool_names(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(self._core_tools.keys() | self._lazy_tools.keys())

    def clone(self, names: set[str] | None = None) -> ToolRegistry:
        """Create a filtered copy of this registry.

        Used by the sub-agent system.  Tool instances are shared.
        """
        child = ToolRegistry()
        for name, tool in self._core_tools.items():
            if names is None or name in names:
                child._core_tools[name] = tool
        for name, tool in self._lazy_tools.items():
            if names is None or name in names:
                child._lazy_tools[name] = tool
        return child

    def __len__(self) -> int:
        return len(self._core_tools) + len(self._lazy_tools)

    def __contains__(self, name: str) -> bool:
        return name in self._core_tools or name in self._lazy_tools
