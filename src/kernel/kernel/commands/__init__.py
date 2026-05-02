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

from kernel.commands.registry import CommandRegistry
from kernel.commands.types import CommandDef
from kernel.protocol.acp.namespaces import AcpMethod, MustangMethod
from kernel.subsystem import Subsystem

__all__ = ["CommandManager", "CommandDef", "CommandRegistry"]

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
        description="List or switch the active LLM model",
        usage="/model [list | switch <name>]",
        acp_method=MustangMethod.MODEL_PROFILE_LIST,
        subcommands=["list", "switch"],
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
        description="Show token usage for the current session",
        usage="/cost",
        acp_method="session/get_usage",
    ),
    CommandDef(
        name="memory",
        description="View or manage long-term memories",
        usage="/memory [list | show <id> | delete <id>]",
        acp_method=None,
        subcommands=["list", "show", "delete"],
    ),
    CommandDef(
        name="auth",
        description="Manage stored credentials",
        usage="/auth set|get|list|delete|import-env ...",
        acp_method=MustangMethod.SECRETS_AUTH,
        subcommands=["set", "get", "list", "delete", "import-env"],
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
        for cmd in _BUILTIN_COMMANDS:
            self._registry.register(cmd)

        # Register user-invocable skills as slash commands.
        self._register_skill_commands()

    async def shutdown(self) -> None:
        """No-op — CommandManager holds no external resources."""

    def lookup(self, name: str) -> CommandDef | None:
        """Return the :class:`CommandDef` for ``name``, or ``None``.

        Args:
            name: Command name without the leading slash.
        """
        return self._registry.lookup(name)

    def list_commands(self) -> list[CommandDef]:
        """Return all registered commands in registration order."""
        return self._registry.list_commands()

    def _register_skill_commands(self) -> None:
        """Register user-invocable skills from SkillManager as commands.

        Called during startup.  Skills become available as
        ``/skill-name`` in the command catalog for client autocomplete.
        """
        try:
            from kernel.skills import SkillManager

            if not self._module_table.has(SkillManager):
                return
            skills_mgr = self._module_table.get(SkillManager)
        except (KeyError, ImportError):
            return

        for skill in skills_mgr.user_invocable_skills():
            name = skill.manifest.name
            # Don't shadow built-in commands.
            if self._registry.lookup(name) is not None:
                continue
            hint = skill.manifest.argument_hint or ""
            usage = f"/{name} {hint}".strip()
            self._registry.register(
                CommandDef(
                    name=name,
                    description=skill.manifest.description,
                    usage=usage,
                    acp_method=None,  # Skills execute via SkillTool, not ACP.
                )
            )
