"""Tools subsystem — built-in tool registry.

Public surface:

- :class:`ToolManager` — Subsystem loaded at step 5 of kernel lifespan,
  owns the :class:`ToolRegistry` + :class:`FileStateCache` and registers
  the built-in tools gated by :class:`ToolFlags`.
- :class:`Tool` — the ABC every built-in / MCP / user tool inherits from.
- :class:`ToolContext` — the single channel through which a Tool touches
  the rest of the kernel.
- :class:`ToolRegistry` / :class:`ToolSnapshot` — registry type + per-turn
  snapshot used by Orchestrator.
- :class:`FileStateCache` — shared state between file-reading and
  file-editing tools.
- Tool-facing types: :class:`PermissionSuggestion`, :class:`ToolCallResult`,
  :class:`ToolCallProgress`, :class:`ToolInputError`, the
  :class:`ToolDisplayPayload` union.

See ``docs/plans/landed/tool-manager.md`` for the full design.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from kernel.subsystem import Subsystem
from kernel.tools.builtin import BUILTIN_TOOLS
from kernel.tools.context import ToolContext
from kernel.tools.file_state import FileState, FileStateCache, hash_text
from kernel.tools.flags import ToolFlags
from kernel.tools.matching import matches_name
from kernel.tools.registry import Layer, ToolRegistry, ToolSnapshot
from kernel.tools.tool import Tool
from kernel.tools.types import (
    DiffDisplay,
    FileDisplay,
    LocationsDisplay,
    PermissionSuggestion,
    RawBlocks,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolDisplayPayload,
    ToolInputError,
)

if TYPE_CHECKING:
    from kernel.module_table import KernelModuleTable

logger = logging.getLogger(__name__)


class ToolManager(Subsystem):
    """Tools subsystem — registry + shared state provider.

    Responsibilities:

    - Register all enabled built-in tools at startup.
    - Own the single ``FileStateCache`` shared across file-* tools.
    - Expose ``snapshot_for_session`` for Orchestrator; filters by
      plan-mode, sub-agent whitelist, and the ToolAuthorizer's
      ``filter_denied_tools`` deny-list.
    - Hand the ``FileStateCache`` out to the Session layer so Orchestrator
      can construct a ``ToolContext`` for each turn.
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        super().__init__(module_table)
        self._flags: ToolFlags | None = None
        self._registry = ToolRegistry()
        self._file_state = FileStateCache()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Register FlagManager section + instantiate enabled built-ins."""
        flag_manager = self._module_table.flags
        try:
            flags = cast(ToolFlags, flag_manager.get_section("tools"))
        except Exception:
            # Not yet registered — register now.  Other subsystems may
            # have already loaded ``ToolFlags`` if they peeked at this
            # section early; the registration is idempotent on schema.
            flag_manager.register("tools", ToolFlags)
            flags = cast(ToolFlags, flag_manager.get_section("tools"))
        self._flags = flags

        prompts = self._module_table.prompts
        self._registry._prompt_manager = prompts

        for tool_cls in BUILTIN_TOOLS:
            if not flags.is_enabled(tool_cls.name):
                logger.info("tool %s disabled via ToolFlags — skipping", tool_cls.name)
                continue
            layer: Layer = "deferred" if tool_cls.should_defer else "core"
            tool = tool_cls()
            tool._prompt_manager = prompts
            self._registry.register(tool, layer=layer, module_table=self._module_table)

        # Register ToolSearchTool — needs a registry reference, so it
        # cannot go through the normal BUILTIN_TOOLS path.
        from kernel.tools.builtin.tool_search import ToolSearchTool

        search_tool = ToolSearchTool(self._registry)
        search_tool._prompt_manager = prompts
        self._registry.register(search_tool, layer="core", module_table=self._module_table)

        # Register ReplTool — needs a registry reference (same pattern
        # as ToolSearchTool).  Only registered when the repl flag is on.
        if flags.repl:
            from kernel.tools.builtin.repl import ReplTool

            repl_tool = ReplTool(self._registry)
            repl_tool._prompt_manager = prompts
            self._registry.register(repl_tool, layer="core", module_table=self._module_table)

        # Wire MCPManager signal so MCP tools are auto-registered
        # when connections come up or change.  MCPManager starts before
        # ToolManager (see app.py), so its initial on_tools_changed has
        # already fired — we must do an immediate sync to pick up any
        # tools from servers that connected during MCPManager.startup().
        self._mcp_disconnect: Any = None
        try:
            from kernel.mcp import MCPManager

            if self._module_table.has(MCPManager):
                mcp = self._module_table.get(MCPManager)
                self._mcp_disconnect = mcp.on_tools_changed.connect(self._sync_mcp)
                # Initial sync — MCPManager already connected its servers.
                await self._sync_mcp()
        except (ImportError, KeyError):
            pass  # MCP subsystem not loaded — no proxy tools.

        # Inject user-configured safe commands into BashTool/PowerShellTool/CmdTool.
        # ToolAuthorizer (step 3) already owns the permissions section via
        # bind_section; we use get_section (read-only view) to avoid the
        # single-writer conflict.
        self._bind_bash_safe_commands()

        logger.info(
            "ToolManager started with %d built-in tools",
            sum(1 for _ in self._registry.all_tools()),
        )

    def _bind_bash_safe_commands(self) -> None:
        """Read ``permissions.bash_safe_commands`` and inject into BashTool.

        Uses ``config.get_section`` (read-only view) because ToolAuthorizer
        already owns this section via ``bind_section``.  Subscribes to the
        ``changed`` signal for hot-reload.
        """
        from kernel.tool_authz.config_section import PermissionsSection

        shell_tool = (
            self._registry.lookup("Bash")
            or self._registry.lookup("PowerShell")
            or self._registry.lookup("Cmd")
        )
        if shell_tool is None or not hasattr(shell_tool, "extra_safe_commands"):
            return

        try:
            section = self._module_table.config.get_section(
                file="config", section="permissions", schema=PermissionsSection
            )
        except Exception:
            logger.debug("ToolManager: could not read permissions section — skipping bash_safe_commands")
            return

        shell_tool.extra_safe_commands = frozenset(section.get().bash_safe_commands)

        async def _on_permissions_changed(
            _old: PermissionsSection, new: PermissionsSection
        ) -> None:
            shell_tool.extra_safe_commands = frozenset(new.bash_safe_commands)  # type: ignore[union-attr]

        section.changed.connect(_on_permissions_changed)

    async def shutdown(self) -> None:
        """Drop registered tools + clear FileStateCache.

        No external resources to release; tools are pure in-process objects.
        """
        if self._mcp_disconnect is not None:
            self._mcp_disconnect()
        self._file_state.clear()
        logger.info("ToolManager: shutdown complete")

    # ------------------------------------------------------------------
    # MCP integration
    # ------------------------------------------------------------------

    async def _sync_mcp(self) -> None:
        """Refresh MCP proxy tools when MCPManager signals a change.

        Called via ``MCPManager.on_tools_changed`` signal.  Clears all
        existing MCP tools and re-registers from connected servers.
        """
        try:
            from kernel.mcp import MCPManager
            from kernel.tools.mcp_adapter import MCPAdapter

            mcp = self._module_table.get(MCPManager)
        except (ImportError, KeyError):
            return

        # 1. Remove old MCP tools.
        mcp_names = [
            name
            for name, (tool, _layer) in list(self._registry._tools.items())
            if name.startswith("mcp__")
        ]
        for name in mcp_names:
            self._registry.unregister(name)

        # 2. Register fresh tools from each connected server.
        registered = 0
        for server in mcp.get_connected():
            tools = await mcp.list_tools(server.name)
            for tool_def in tools:
                adapter = MCPAdapter(server.name, tool_def, mcp)
                adapter._prompt_manager = self._module_table.prompts
                try:
                    # Register as core (not deferred) — ToolSearchTool
                    # is not implemented yet, so deferred tools would be
                    # invisible to the LLM.  Move to "deferred" once
                    # ToolSearch lands.
                    self._registry.register(adapter, layer="core")
                    registered += 1
                except ValueError as exc:
                    logger.warning("_sync_mcp: %s", exc)

        # 3. Register auth pseudo-tools for NeedsAuth servers.
        auth_registered = 0
        secrets = self._module_table.secrets
        if secrets is not None:
            from kernel.mcp.types import NeedsAuthServer
            from kernel.tools.builtin.mcp_auth import McpAuthTool

            for conn in mcp.get_connections().values():
                if isinstance(conn, NeedsAuthServer) and conn.server_url:
                    auth_tool = McpAuthTool(
                        conn.name, conn.server_url, mcp, secrets
                    )
                    auth_tool._prompt_manager = self._module_table.prompts
                    try:
                        self._registry.register(auth_tool, layer="core")
                        auth_registered += 1
                    except ValueError as exc:
                        logger.warning("_sync_mcp auth tool: %s", exc)

        logger.info(
            "ToolManager: synced %d MCP tools + %d auth tools",
            registered, auth_registered,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_tool(self, tool: Tool, *, layer: Layer = "core") -> None:
        """Register an external tool (e.g. from MemoryManager).

        Resolves and caches the input schema automatically via
        ``module_table``.  This is the public API for subsystems that
        need to add tools after ToolManager startup.
        """
        tool._prompt_manager = self._module_table.prompts
        self._registry.register(tool, layer=layer, module_table=self._module_table)

    def lookup(self, name: str) -> Tool | None:
        """Resolve a name (primary or alias) to a Tool instance."""
        return self._registry.lookup(name)

    def file_state(self) -> FileStateCache:
        """Return the shared ``FileStateCache`` for use in ``ToolContext``."""
        return self._file_state

    def snapshot_for_session(
        self,
        *,
        session_id: str,
        plan_mode: bool = False,
        agent_whitelist: set[str] | None = None,
    ) -> ToolSnapshot:
        """Build a per-turn snapshot of visible tools.

        Consults ``ToolAuthorizer.filter_denied_tools`` (when available)
        to strip deny-listed tools from the pool entirely — the LLM
        never sees them.  Session-level defense-in-depth with
        ``ToolAuthorizer.authorize()``, which is also called at each
        tool-call site.

        ``session_id`` is reserved for future per-session policy
        (e.g. rate limits); currently unused.
        """
        denied: set[str] = set()
        # ToolAuthorizer is step 3, ToolManager is step 5 — authorizer
        # is always up by the time we snapshot, except in degraded mode
        # where it failed to load.  Handle the missing case gracefully.
        try:
            from kernel.tool_authz import ToolAuthorizer

            authorizer = self._module_table.get(ToolAuthorizer)
        except (KeyError, ImportError):
            authorizer = None

        if authorizer is not None:
            all_names = {tool.name for tool, _ in self._registry.all_tools()}
            denied = authorizer.filter_denied_tools(all_names)

        repl_mode = self._flags is not None and self._flags.repl
        return self._registry.snapshot(
            plan_mode=plan_mode,
            repl_mode=repl_mode,
            agent_whitelist=agent_whitelist,
            denied_names=denied,
        )


__all__ = [
    "DiffDisplay",
    "FileDisplay",
    "FileState",
    "FileStateCache",
    "Layer",
    "LocationsDisplay",
    "PermissionSuggestion",
    "RawBlocks",
    "TextDisplay",
    "Tool",
    "ToolCallProgress",
    "ToolCallResult",
    "ToolContext",
    "ToolDisplayPayload",
    "ToolFlags",
    "ToolInputError",
    "ToolManager",
    "ToolRegistry",
    "ToolSnapshot",
    "hash_text",
    "matches_name",
]
