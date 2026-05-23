"""Command definition types for the CommandManager subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
        source: Where the command came from.  ``"skill"`` commands are
            projections of user-invocable skills and execute through the
            Kernel skill activation path, not local CLI file reads.
        aliases: Deprecated or compatibility names accepted for lookup
            but not returned as standalone catalog entries.
        canonical_name: Stable display name when ``name`` is an aliasable
            projection.  Defaults to ``name``.
        metadata: Structured command metadata used by clients and gateways
            so they do not have to parse command strings.
    """

    name: str
    description: str
    usage: str
    acp_method: str | None
    subcommands: list[str] = field(default_factory=list)
    source: str = "builtin"
    aliases: list[str] = field(default_factory=list)
    canonical_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
