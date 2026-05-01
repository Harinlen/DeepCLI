"""In-memory registry for slash command definitions."""

from __future__ import annotations

from kernel.commands.types import CommandDef


class CommandRegistry:
    """Thread-safe (asyncio-safe) registry of :class:`CommandDef` objects.

    Commands are keyed by name.  The registry is append-only during
    startup; no runtime mutation after ``CommandManager.startup()``
    completes.

    Raises:
        ValueError: If a command with the same name is registered twice.
    """

    def __init__(self) -> None:
        self._commands: dict[str, CommandDef] = {}

    def register(self, cmd: CommandDef) -> None:
        """Add a command definition to the registry.

        Args:
            cmd: The command to register.

        Raises:
            ValueError: If a command named ``cmd.name`` already exists.
        """
        if cmd.name in self._commands:
            raise ValueError(f"Command already registered: {cmd.name!r}")
        self._commands[cmd.name] = cmd

    def lookup(self, name: str) -> CommandDef | None:
        """Return the :class:`CommandDef` for ``name``, or ``None``.

        Args:
            name: Command name without the leading slash.
        """
        return self._commands.get(name)

    def list_commands(self) -> list[CommandDef]:
        """Return all registered commands in registration order."""
        return list(self._commands.values())
