"""Extension manager — discover and load tools/skills/hooks/MCP.

Loads built-in tools, user-defined tools, skills, hooks, and MCP
server connections.  ``load_all()`` is the single entry point called
during daemon startup.

Each subsystem registers its own cleanup callbacks via
``daemon.lifecycle.register_cleanup`` so shutdown is fully decoupled
from the loading code.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from daemon.config.schema import RuntimeConfig
from daemon.extensions.health_monitor import start_health_monitor
from daemon.extensions.hook_config_parser import parse_hook_config
from daemon.extensions.hooks.registry import HookRegistry
from daemon.extensions.mcp.bridge import McpBridge
from daemon.extensions.mcp.client import McpClient
from daemon.extensions.mcp.config import McpServerEntry, load_mcp_config
from daemon.extensions.mcp.transport import create_transport
from daemon.extensions.tools.result_store import ResultStore
from daemon.extensions.skills.loader import discover_skills
from daemon.extensions.skills.registry import SkillRegistry
from daemon.extensions.tools.base import ToolContext
from daemon.extensions.tools.builtin import get_builtin_tools
from daemon.extensions.tools.builtin.skill_tool import SkillTool
from daemon.extensions.tools.builtin.tool_search import ToolSearchTool
from daemon.extensions.tools.registry import ToolRegistry
from daemon.extensions.tools.user_loader import load_user_tools
from daemon.lifecycle import register_cleanup

logger = logging.getLogger(__name__)

# Default directories
USER_TOOLS_DIR = Path.home() / ".mustang" / "tools"
USER_SKILLS_DIR = Path.home() / ".mustang" / "skills"
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent / "skills" / "bundled"
MCP_JSON_PATH = Path.home() / ".mustang" / "mcp.json"
TOOL_RESULT_CACHE_DIR = Path.home() / ".mustang" / "cache" / "tool_results"


class ExtensionManager:
    """Discovers and loads all extensions (tools, skills, hooks, MCP).

    Loads built-in tools, user-defined tools, skills, hooks, and MCP
    server connections.  ``load_all()`` orchestrates the full sequence.
    Each subsystem registers cleanup callbacks via the lifecycle module
    so shutdown is handled by ``lifecycle.run_cleanups()``.

    Attributes:
        tool_registry: The shared tool registry with all loaded tools.
        skill_registry: The shared skill registry with all loaded skills.
        hook_registry: The shared hook registry with all loaded hooks.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        user_tools_dir: Path | None = None,
        skill_dirs: list[Path] | None = None,
        mcp_json_path: Path | None = None,
        result_cache_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._user_tools_dir = user_tools_dir or USER_TOOLS_DIR
        self._skill_dirs = skill_dirs if skill_dirs is not None else self._default_skill_dirs()
        self._mcp_json_path = mcp_json_path or MCP_JSON_PATH

        # Shared result store — used by orchestrator (budget) and MCP bridge
        self._result_cache_dir = result_cache_dir or TOOL_RESULT_CACHE_DIR
        self.result_store = ResultStore(self._result_cache_dir)

        self.tool_registry = ToolRegistry()
        self.skill_registry = SkillRegistry()
        self.hook_registry = HookRegistry()

        # MCP state (populated by load_mcp_servers)
        self._mcp_clients: list[McpClient] = []
        self._mcp_bridges: list[McpBridge] = []

        # Health monitor task
        self._health_task: asyncio.Task[None] | None = None

    def _default_skill_dirs(self) -> list[Path]:
        """Build default skill directories in priority order.

        Order (high → low): project → user → bundled.
        Project-level is relative to cwd (resolved at load time).
        User/project skills shadow bundled ones with the same name.
        """
        return [
            Path.cwd() / ".mustang" / "skills",  # project-level
            USER_SKILLS_DIR,  # user-level
            BUNDLED_SKILLS_DIR,  # bundled (shipped with Mustang)
        ]

    def load_builtin_tools(self) -> None:
        """Register all built-in tools.

        Delegates to ``get_builtin_tools()`` so this method does not
        need to know about individual tool classes.
        """
        tools = get_builtin_tools(self._config)

        for tool in tools:
            self.tool_registry.register(tool)

        logger.info(
            "Loaded %d built-in tools: %s",
            len(tools),
            ", ".join(t.name for t in tools),
        )

    def load_user_tools(self) -> None:
        """Discover and register user-defined tools from ~/.mustang/tools/.

        Scans the user tools directory for Python files containing Tool
        subclasses.  Errors in individual files are logged and skipped.
        Duplicate names (conflicting with built-in tools) are warned
        and skipped.
        """
        user_tools = load_user_tools(self._user_tools_dir)
        loaded = 0
        for tool in user_tools:
            if tool.name in self.tool_registry:
                logger.warning(
                    "User tool '%s' conflicts with existing tool, skipping",
                    tool.name,
                )
                continue
            self.tool_registry.register(tool)
            loaded += 1

        if loaded:
            logger.info("Loaded %d user-defined tools", loaded)

    def load_skills(self) -> None:
        """Discover and register skills from configured directories.

        Scans skill directories in priority order.  Only reads
        frontmatter at this stage (bodies are lazy-loaded on demand).
        If skills are found, also registers the SkillTool so the LLM
        can activate them.
        """
        skills = discover_skills(self._skill_dirs)
        registered = 0
        disabled = set(self._config.skills.disabled)

        for skill in skills:
            if skill.name in disabled:
                logger.debug("Skill '%s' is disabled, skipping", skill.name)
                continue
            if self.skill_registry.register(skill):
                registered += 1

        if registered:
            logger.info(
                "Loaded %d skills: %s",
                registered,
                ", ".join(self.skill_registry.skill_names),
            )

            # Register SkillTool so the LLM can invoke skills
            if "skill" not in self.tool_registry:
                self.tool_registry.register(SkillTool(self.skill_registry))

    def load_hooks(self) -> None:
        """Parse hook definitions from config and register them.

        Converts ``HookRuntimeConfig`` entries from the resolved config
        into internal ``HookConfig`` objects and registers them in the
        hook registry.  Invalid event/type values are logged and skipped.
        """
        loaded = 0
        for hook_cfg in self._config.hooks:
            hook = parse_hook_config(hook_cfg)
            if hook is None:
                continue
            self.hook_registry.register(hook)
            loaded += 1

        if loaded:
            logger.info("Loaded %d hooks", loaded)

    async def load_mcp_servers(self) -> None:
        """Connect to configured MCP servers and register their tools.

        Reads server definitions from ``~/.mustang/mcp.json`` and
        ``config.yaml``, connects to each via stdio, and registers
        proxy tools.  Failures are logged but do not block startup.

        Each connected client registers its own cleanup callback, and
        a background health monitor detects unexpected process exits.
        """
        entries = load_mcp_config(self._mcp_json_path, self._config.mcp_servers)
        if not entries:
            return

        async def _connect_one(entry: "McpServerEntry") -> tuple[McpClient, McpBridge] | None:
            """Connect a single MCP server (for asyncio.gather)."""
            transport = create_transport(entry)
            client = McpClient(transport, server_name=entry.name)
            bridge = McpBridge(
                client,
                self.tool_registry,
                tools_concurrency=entry.tools_concurrency,
            )

            # Wire reconnect callback so tools refresh automatically
            client.on_reconnect = bridge.sync_tools  # type: ignore[assignment]

            try:
                await client.connect()
                await bridge.sync_tools()

                # Self-registering cleanup: each client manages its own shutdown
                async def _cleanup_client(
                    c: McpClient = client,
                    b: McpBridge = bridge,
                ) -> None:
                    """Unregister MCP tools and close the client."""
                    for name in b.get_tool_names():
                        self.tool_registry.unregister(name)
                    try:
                        await c.close()
                    except Exception:
                        logger.warning(
                            "Error closing MCP server '%s'",
                            c.server_name,
                            exc_info=True,
                        )

                register_cleanup(_cleanup_client)
                return client, bridge
            except Exception:
                logger.warning(
                    "MCP server '%s' failed to connect — skipping",
                    entry.name,
                    exc_info=True,
                )
                # Clean up the failed client
                try:
                    await client.close()
                except Exception:
                    pass
                return None

        # Connect all servers concurrently
        results = await asyncio.gather(
            *(_connect_one(e) for e in entries),
            return_exceptions=True,
        )

        for res in results:
            if isinstance(res, tuple):
                client, bridge = res
                self._mcp_clients.append(client)
                self._mcp_bridges.append(bridge)
            elif isinstance(res, Exception):
                logger.warning("MCP connection task failed: %s", res)

        if self._mcp_clients:
            total_tools = sum(len(b.get_tool_names()) for b in self._mcp_bridges)
            logger.info(
                "Loaded %d MCP servers (%d tools)",
                len(self._mcp_clients),
                total_tools,
            )

        # Start health monitor for all connected MCP servers
        if self._mcp_clients:
            self._start_health_monitor()

    def _start_health_monitor(self) -> None:
        """Spawn the background MCP health-check task.

        Thin delegate to :func:`health_monitor.start_health_monitor`
        — keeps the monitor reference so callers can introspect it
        for debugging / testing.
        """
        self._health_task = start_health_monitor(self._mcp_clients)

    async def load_all(self) -> None:
        """Load all extension types in order.

        Call this during daemon startup.  Order: result store cleanup →
        built-in tools → user tools → skills → hooks → MCP servers.
        SkillTool is auto-registered when skills are found.
        """
        self.result_store.cleanup_on_startup()
        self.load_builtin_tools()
        self.load_user_tools()
        self.load_skills()
        self.load_hooks()
        await self.load_mcp_servers()

        # Register ToolSearchTool last — it needs the populated registry.
        if "tool_search" not in self.tool_registry:
            self.tool_registry.register(ToolSearchTool(registry=self.tool_registry))

    def default_tool_context(self, cwd: str | None = None) -> ToolContext:
        """Build a default ToolContext from config.

        Args:
            cwd: Override working directory.  Defaults to ``"."``.

        Returns:
            ToolContext for tool execution.
        """
        return ToolContext(cwd=cwd or ".")
