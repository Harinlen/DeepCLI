"""Command definition types for the CommandManager subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandDef:
    """Definition of a single slash command.

    Consumed by WS clients to build their local command registry (used
    for autocomplete and ``/help`` rendering).  Also used by
    ``GatewayAdapter._execute_for_channel`` to dispatch gateway-side
    commands without a WebSocket connection.

    Attributes:
        name: Command name without the leading slash (e.g. ``"model"``).
        description: One-line description shown in ``/help``.
        usage: Usage pattern (e.g. ``"/model [list | switch <name>]"``).
        acp_method: ACP method the WS client calls, or ``None`` for
            purely local commands (e.g. ``/help``).
        subcommands: Optional list of subcommand names for autocomplete.
    """

    name: str
    description: str
    usage: str
    acp_method: str | None
    subcommands: list[str] = field(default_factory=list)
