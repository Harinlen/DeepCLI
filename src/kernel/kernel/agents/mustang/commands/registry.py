"""In-memory registry for slash command definitions."""

from __future__ import annotations

from kernel.agents.mustang.commands.types import CommandDef


class CommandRegistry:
    """Thread-safe (asyncio-safe) registry of :class:`CommandDef` objects.

    Commands are keyed by name.  Built-ins are stable, while skill
    commands are a projection that CommandManager may rebuild when
    SkillManager reports discovery changes.

    Raises:
        ValueError: If a command with the same name is registered twice.
    """

    def __init__(self) -> None:
        self._commands: dict[str, CommandDef] = {}
        self._aliases: dict[str, str] = {}

    def register(self, cmd: CommandDef) -> None:
        """Add a command definition to the registry.

        Args:
            cmd: The command to register.

        Raises:
            ValueError: If a command named ``cmd.name`` already exists.
        """
        if cmd.name in self._commands or cmd.name in self._aliases:
            raise ValueError(f"Command already registered: {cmd.name!r}")
        for alias in cmd.aliases:
            if alias in self._commands or alias in self._aliases:
                raise ValueError(f"Command alias already registered: {alias!r}")
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._aliases[alias] = cmd.name

    def lookup(self, name: str) -> CommandDef | None:
        """Return the :class:`CommandDef` for ``name``, or ``None``.

        Args:
            name: Command name without the leading slash.
        """
        canonical = self._aliases.get(name, name)
        return self._commands.get(canonical)

    def list_commands(self) -> list[CommandDef]:
        """Return all registered commands in registration order."""
        return list(self._commands.values())

    def clear(self) -> None:
        """Remove all command definitions."""
        self._commands.clear()
        self._aliases.clear()
