"""CommandManager — command catalog provider.

Maintains the registry of built-in slash command definitions.  It is a
*directory provider*, not an executor: WS clients pull the catalog via
``commands/list`` and dispatch commands themselves via existing ACP
primitives; ``GatewayAdapter`` calls ``lookup()`` and routes to the
appropriate kernel internal method directly.

There is deliberately no ``dispatch()`` here — command execution always
flows through existing mechanisms (ACP session methods for WS clients,
direct kernel API calls for gateway adapters).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from kernel.agents.mustang.commands.registry import CommandRegistry
from kernel.agents.mustang.commands.types import CommandDef
from kernel.core.protocol.acp.namespaces import AcpMethod, MustangMethod
from kernel.core.lifecycle import Subsystem

__all__ = ["CommandManager", "CommandDef", "CommandRegistry"]

logger = logging.getLogger(__name__)

# Built-in slash commands.  ``acp_method`` is the ACP method a WS client
# calls; ``None`` means the command is handled client-side (e.g. /help).
_BUILTIN_COMMANDS: list[CommandDef] = [
    CommandDef(
        name="help",
        description="Show available commands",
        usage="/help",
        acp_method=None,
    ),
    CommandDef(
        name="model",
        description="Manage LLM models",
        usage="/model [list | add | current | use]",
        acp_method=MustangMethod.MODEL_PROFILE_LIST,
        subcommands=["list", "add", "current", "use"],
    ),
    CommandDef(
        name="plan",
        description="Enter, exit, or query plan mode",
        usage="/plan [enter | exit | status]",
        acp_method=AcpMethod.SESSION_SET_MODE,
        subcommands=["enter", "exit", "status"],
    ),
    CommandDef(
        name="compact",
        description="Summarise conversation history to free context",
        usage="/compact",
        acp_method="session/compact",
    ),
    CommandDef(
        name="session",
        description="Manage sessions: list, resume, or delete",
        usage="/session [list | resume <id> | delete <id>]",
        acp_method=AcpMethod.SESSION_LIST,
        subcommands=["list", "resume", "delete"],
    ),
    CommandDef(
        name="cost",
        description="Show context and token usage for the current session",
        usage="/cost",
        acp_method=MustangMethod.SESSION_GET_USAGE,
    ),
    CommandDef(
        name="cron",
        description="View or manage scheduled cron jobs",
        usage="/cron [list | create | delete]",
        acp_method=None,
        subcommands=["list", "create", "delete"],
    ),
    CommandDef(
        name="memory",
        description="View or manage long-term memories",
        usage="/memory [list | show <id> | delete <id>]",
        acp_method=None,
        subcommands=["list", "show", "delete"],
    ),
    CommandDef(
        name="kernel",
        description="Inspect or restart the local DeepCLI runtime",
        usage="/kernel [status | restart]",
        acp_method=MustangMethod.RUNTIME_STATUS,
        subcommands=["status", "restart"],
    ),
    CommandDef(
        name="webfetch",
        description="Manage WebFetch backend and backend config",
        usage="/webfetch [backend | config]",
        acp_method=MustangMethod.WEB_FETCH_BACKEND_OPTIONS,
        subcommands=["backend", "config"],
    ),
    CommandDef(
        name="global",
        description="Backup, export, or dry-run import global ResourceStore data",
        usage="/global [backup | backups | export | import | restore]",
        acp_method=MustangMethod.GLOBAL_BACKUP,
        subcommands=["backup", "backups", "export", "import", "restore"],
    ),
    CommandDef(
        name="flag",
        description="Read or stage startup flag changes for the next restart",
        usage="/flag [list | read | set | reset]",
        acp_method=MustangMethod.FLAGS_LIST,
        subcommands=["list", "read", "set", "reset"],
    ),
    CommandDef(
        name="secrets",
        description="List, audit, rename, or delete stored secret metadata",
        usage="/secrets [list | audit | rename | delete]",
        acp_method=MustangMethod.SECRETS_LIST,
        subcommands=["list", "audit", "rename", "delete"],
    ),
    CommandDef(
        name="agents",
        description="Manage durable agents, bindings, lifecycle, and grants",
        usage="/agents [list | add | delete | set-identity | bindings | bind | unbind | start | stop | restart | health | grants | grant | revoke-grant]",
        acp_method=MustangMethod.AGENTS_LIST,
        subcommands=[
            "list",
            "add",
            "delete",
            "set-identity",
            "bindings",
            "bind",
            "unbind",
            "start",
            "stop",
            "restart",
            "health",
            "grants",
            "grant",
            "revoke-grant",
        ],
    ),
    CommandDef(
        name="agent",
        description="Send a message to a routed durable agent",
        usage="/agent send <agent-id> <message>",
        acp_method=MustangMethod.AGENT_SEND,
        subcommands=["send"],
    ),
    CommandDef(
        name="gateways",
        description="Manage Access Router gateways and channel bindings",
        usage="/gateways [list | create | status | delete | enable | disable | reload | bindings | bind | unbind]",
        acp_method=MustangMethod.GATEWAYS_LIST,
        subcommands=[
            "list",
            "create",
            "status",
            "delete",
            "enable",
            "disable",
            "reload",
            "bindings",
            "bind",
            "unbind",
        ],
    ),
    CommandDef(
        name="skills",
        description="Manage skills and skill-installed commands",
        usage="/skills [list | inspect | search | sources | install | refresh | check | update | audit | uninstall]",
        acp_method=MustangMethod.SKILLS_LIST,
        subcommands=[
            "list",
            "inspect",
            "search",
            "sources",
            "install",
            "refresh",
            "check",
            "update",
            "audit",
            "uninstall",
        ],
    ),
    CommandDef(
        name="mcp",
        description="Manage global MCP server declarations",
        usage="/mcp [list | read | create | update | delete]",
        acp_method=MustangMethod.MCP_LIST,
        subcommands=["list", "read", "create", "update", "delete"],
    ),
]


class CommandManager(Subsystem):
    """Slash command catalog provider.

    Startup
    -------
    Registers all built-in :class:`CommandDef` objects into a
    :class:`CommandRegistry`.  No flags, no config section, no external
    resources — startup is always synchronous and infallible.

    Public API
    ----------
    ``lookup(name)``       — find a command by name
    ``list_commands()``    — return all registered commands
    """

    async def startup(self) -> None:
        """Populate the command registry with built-in + skill commands."""
        self._registry = CommandRegistry()
        self._commands_changed_callbacks: list[Any] = []
        self._skill_command_names: set[str] = set()
        self._rebuild_registry()
        self._subscribe_to_skill_changes()

    async def shutdown(self) -> None:
        """Clear in-memory registry and callbacks."""
        self._registry.clear()
        self._commands_changed_callbacks.clear()
        self._skill_command_names.clear()

    def lookup(self, name: str) -> CommandDef | None:
        """Return the :class:`CommandDef` for ``name``, or ``None``.

        Args:
            name: Command name without the leading slash.
        """
        return self._registry.lookup(name)

    def list_commands(self) -> list[CommandDef]:
        """Return all registered commands in registration order."""
        return self._registry.list_commands()

    def list_command_dicts(self) -> list[dict[str, Any]]:
        """Return the command catalog as JSON-serialisable dicts."""
        return [asdict(cmd) for cmd in self.list_commands()]

    def on_commands_changed(self, callback: Any) -> None:
        """Register a callback invoked after the command catalog changes."""
        self._commands_changed_callbacks.append(callback)

    def _rebuild_registry(self) -> None:
        """Rebuild built-in + skill command projection from current truth."""
        registry = CommandRegistry()
        for cmd in _BUILTIN_COMMANDS:
            registry.register(cmd)

        self._registry = registry
        self._skill_command_names.clear()
        self._register_skill_commands()

    def _register_skill_commands(self) -> None:
        """Register user-invocable skills from SkillManager as commands.

        Called during startup.  Skills become available as
        ``/skill:<name>`` in the command catalog for client autocomplete.
        """
        try:
            from kernel.agents.mustang.skills import SkillManager

            if not self._module_table.has(SkillManager):
                return
            skills_mgr = self._module_table.get(SkillManager)
        except (KeyError, ImportError):
            return

        for skill in skills_mgr.user_invocable_skills():
            name = skill.manifest.name
            command_name = f"skill:{name}"
            if self._registry.lookup(command_name) is not None:
                continue
            aliases = [] if self._registry.lookup(name) is not None else [name]
            hint = skill.manifest.argument_hint or ""
            usage = f"/{command_name} {hint}".strip()
            self._registry.register(
                CommandDef(
                    name=command_name,
                    description=skill.manifest.description,
                    usage=usage,
                    acp_method=MustangMethod.SESSION_ACTIVATE_SKILL,
                    source="skill",
                    aliases=aliases,
                    canonical_name=command_name,
                    metadata={
                        "kind": "skill",
                        "skillName": name,
                        "canonicalCommand": command_name,
                        "compatAliases": aliases,
                    },
                )
            )
            self._skill_command_names.add(command_name)

    def _subscribe_to_skill_changes(self) -> None:
        """Subscribe to SkillManager changes when the subsystem is present."""
        try:
            from kernel.agents.mustang.skills import SkillManager

            if not self._module_table.has(SkillManager):
                return
            skills_mgr = self._module_table.get(SkillManager)
            skills_mgr.on_skills_changed(self._on_skills_changed)
        except (KeyError, ImportError):
            return

    def _on_skills_changed(self) -> None:
        """Refresh skill commands after dynamic skill discovery changes."""
        before = self.list_command_dicts()
        self._rebuild_registry()
        after = self.list_command_dicts()
        if before == after:
            return
        logger.info("CommandManager: command catalog changed (%d commands)", len(after))
        for callback in list(self._commands_changed_callbacks):
            try:
                callback(after)
            except Exception:
                logger.exception("commands_changed callback failed")
