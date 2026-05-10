"""Session and UI state events emitted by Orchestrator-adjacent flows.

The Orchestrator owns conversation state, while Session owns ACP projection.
These small events form the hand-off boundary for UI-visible state changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanUpdate:
    """Full snapshot of the current plan.

    The plan file is durable storage; the event carries a rendered snapshot so
    clients do not need filesystem access to stay in sync.
    """

    entries: list[dict[str, Any]]


@dataclass(frozen=True)
class ModeChanged:
    """Emitted when the permission mode changes.

    ``mode_id`` is a string for ACP compatibility; validation happens at the
    Orchestrator/ToolAuthorizer boundary where the known modes live.
    """

    mode_id: str


@dataclass(frozen=True)
class ConfigOptionChanged:
    """Full snapshot of user-visible config options.

    This is a snapshot rather than a patch because clients may connect mid-turn
    and should be able to replace local state without replaying older deltas.
    """

    options: dict[str, Any]


@dataclass(frozen=True)
class SessionInfoChanged:
    """Partial update for session metadata visible to the client.

    Metadata evolves independently from conversation history; keeping it a
    separate event avoids forcing clients to inspect history rows for titles.
    """

    title: str | None = None


@dataclass(frozen=True)
class AvailableCommandsChanged:
    """Emitted when the slash-command catalog changes.

    Command availability may depend on tools, skills, and config.  The event is
    intentionally a full list so clients can rebuild autocomplete deterministically.
    """

    commands: list[dict[str, Any]]
