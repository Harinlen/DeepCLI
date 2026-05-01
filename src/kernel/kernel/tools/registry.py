"""ToolRegistry — core + deferred two-layer tool catalog.

Phase 1 populates only the ``core`` layer with six built-in tools;
the ``deferred`` layer is implemented but left empty because the
prompt-budget win from deferred schemas only matters at 15+ tools
(Claude Code has 43+).

Responsibilities
----------------
- Maintain ``name -> Tool`` + ``alias -> Tool`` lookup maps.
- Compute ``ToolSnapshot`` for each turn (filtered by feature flags,
  plan mode, optional sub-agent whitelist, and ToolAuthorizer's
  deny-list).
- Hand tools to the Orchestrator's ToolExecutor via the snapshot's
  ``lookup`` table.

The registry is **not** a Subsystem — it's an internal data structure
owned by ``ToolManager``.  Lifecycle + flag binding happens in
``ToolManager.startup``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from kernel.tools.matching import matches_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from kernel.llm.types import ToolSchema
    from kernel.module_table import KernelModuleTable
    from kernel.orchestrator.types import ToolKind
    from kernel.prompts import PromptManager
    from kernel.tools.tool import Tool

logger = logging.getLogger(__name__)


Layer = Literal["core", "deferred"]


@dataclass(frozen=True)
class ToolSnapshot:
    """The tool pool visible for one LLM turn.

    ``schemas`` is sorted deterministically so the prompt-cache prefix
    stays stable across turns (mirrors Claude Code ``tools.ts:345-390``):
    core tools alphabetical first, then deferred stubs alphabetical,
    then MCP tools alphabetical.
    """

    schemas: list[ToolSchema]
    """Fed to ``provider.stream(tool_schemas=...)``."""

    lookup: dict[str, Tool]
    """Both primary names and aliases, Orchestrator uses this to resolve
    ``ToolUseContent.name`` during execution."""

    deferred_names: set[str]
    """Names of tools surfaced by stub only (schema withheld)."""

    deferred_listing: str
    """Pre-formatted text listing deferred tool names for system-prompt
    injection.  Empty string when no deferred tools exist."""


class ToolRegistry:
    """Core + deferred registry with deterministic snapshot ordering."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[Tool, Layer]] = {}
        self._prompt_manager: PromptManager | None = None
        """Primary-name -> (tool, layer).  Aliases are resolved via
        ``matches_name`` in ``lookup``, not stored separately."""

    def register(
        self,
        tool: Tool,
        *,
        layer: Layer = "core",
        module_table: KernelModuleTable | None = None,
    ) -> None:
        """Add a tool to the registry.

        When ``module_table`` is provided, the tool's input schema is
        resolved via ``build_input_schema(module_table)`` and cached on
        the instance.  This ensures ``to_schema()`` returns the resolved
        schema on every subsequent call.  Callers that omit
        ``module_table`` must cache the schema themselves (legacy path).

        Raises ``ValueError`` when a tool with the same primary name is
        already registered, or when an alias collides with another
        tool's primary name.
        """
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        for alias in tool.aliases:
            if alias in self._tools:
                raise ValueError(f"tool {tool.name!r} alias {alias!r} conflicts with existing tool")

        # Auto-cache input schema when module_table is available.
        if module_table is not None and not hasattr(tool, "_cached_input_schema"):
            resolved = type(tool).build_input_schema(module_table)
            object.__setattr__(tool, "_cached_input_schema", resolved)

        self._tools[tool.name] = (tool, layer)
        logger.debug("registered tool %s (layer=%s)", tool.name, layer)

    def promote(self, name: str) -> bool:
        """Move a deferred tool to the core layer.

        After promotion the tool's full schema will appear in the next
        ``snapshot()`` call.  Returns ``True`` when the tool was actually
        promoted, ``False`` when the name is unknown or already core.
        """
        if name in self._tools:
            tool, layer = self._tools[name]
            if layer == "deferred":
                self._tools[name] = (tool, "core")
                logger.debug("promoted tool %s from deferred → core", name)
                return True
        return False

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry.

        No-op if ``name`` is unknown.  Used by ``MCPAdapter`` when a
        remote tool goes away; built-in tools never call this.
        """
        self._tools.pop(name, None)

    def lookup(self, name: str) -> Tool | None:
        """Resolve ``name`` (primary or alias) to a Tool instance.

        Uses ``matches_name`` so aliased names resolve to the primary
        tool; returns ``None`` when nothing matches.
        """
        for tool, _layer in self._tools.values():
            if matches_name(tool, name):
                return tool
        return None

    def all_tools(self) -> Iterable[tuple[Tool, Layer]]:
        """Iterate over every registered ``(tool, layer)`` pair.

        Ordering is registration order — stable for prompt caching.
        """
        return self._tools.values()

    def snapshot(
        self,
        *,
        plan_mode: bool = False,
        repl_mode: bool = False,
        agent_whitelist: set[str] | None = None,
        denied_names: set[str] | None = None,
    ) -> ToolSnapshot:
        """Build a ``ToolSnapshot`` for the next LLM turn.

        Filters:
          - ``plan_mode``: excludes mutating kinds (edit / delete / move /
            execute) so the LLM can't emit a write call in the first place
            (defense-in-depth with ToolAuthorizer's plan-mode rejection).
            ``orchestrate`` (AgentTool) and ``other`` (ExitPlanMode) are
            intentionally NOT mutating — they survive plan-mode, matching
            CC's behavior where Agent stays visible in plan mode.
          - ``repl_mode``: hides primitive tools from the LLM's schema
            list (they are only accessible via the REPL tool).  Hidden
            tools remain in ``lookup`` so REPL can dispatch internally.
          - ``agent_whitelist``: when a sub-agent is scoped to a subset
            of tools, only those names pass through.
          - ``denied_names``: ToolAuthorizer's ``filter_denied_tools``
            output — tools fully blocked by deny rules are stripped
            from the LLM's view entirely.
        """
        # Lazy import to avoid circular dependency at module load.
        if repl_mode:
            from kernel.tools.builtin.repl import REPL_HIDDEN_TOOLS
        else:
            REPL_HIDDEN_TOOLS = frozenset()  # noqa: N806

        denied = denied_names or set()
        core: list[Tool] = []
        deferred_stubs: list[str] = []
        lookup: dict[str, Tool] = {}

        for tool, layer in self._tools.values():
            if tool.name in denied:
                continue
            if agent_whitelist is not None and tool.name not in agent_whitelist:
                continue
            if plan_mode and _is_mutating(tool.kind):
                continue

            hidden_by_repl = repl_mode and tool.name in REPL_HIDDEN_TOOLS

            if not hidden_by_repl:
                # Tool is visible to the LLM — add to schemas.
                if layer == "deferred" and not tool.always_load:
                    deferred_stubs.append(tool.name)
                else:
                    core.append(tool)

            # Always add to lookup — REPL dispatches internally via
            # registry.lookup(), so hidden tools must be resolvable.
            lookup[tool.name] = tool
            for alias in tool.aliases:
                lookup[alias] = tool

        # Deterministic ordering: core alphabetical → deferred stubs alphabetical.
        core.sort(key=lambda t: t.name)
        deferred_stubs.sort()

        schemas = [t.to_schema() for t in core]

        # Build a human-readable listing of deferred tool names for
        # injection into the system prompt as a <system-reminder>.
        listing = ""
        if deferred_stubs:
            if self._prompt_manager is not None and self._prompt_manager.has(
                "orchestrator/deferred_tools"
            ):
                listing = self._prompt_manager.render(
                    "orchestrator/deferred_tools",
                    tool_names="\n".join(deferred_stubs),
                )
            else:
                listing = (
                    "The following deferred tools are now available via ToolSearch. "
                    "Their schemas are NOT loaded \u2014 calling them directly will "
                    "fail with InputValidationError. Use ToolSearch with query "
                    '"select:<name>[,<name>...]" to load tool schemas before '
                    "calling them:\n" + "\n".join(deferred_stubs)
                )

        return ToolSnapshot(
            schemas=schemas,
            lookup=lookup,
            deferred_names=set(deferred_stubs),
            deferred_listing=listing,
        )


_MUTATING_KINDS: set[ToolKind] = set()


def _is_mutating(kind: ToolKind) -> bool:
    # Lazy init — avoid importing ToolKind at module load to dodge
    # any future circular-import shenanigans.
    global _MUTATING_KINDS
    if not _MUTATING_KINDS:
        from kernel.orchestrator.types import ToolKind as TK

        _MUTATING_KINDS = {TK.edit, TK.delete, TK.move, TK.execute}
    return kind in _MUTATING_KINDS


__all__ = ["Layer", "ToolRegistry", "ToolSnapshot"]
