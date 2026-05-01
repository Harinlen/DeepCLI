"""Stop reasons for Orchestrator query turns."""

from __future__ import annotations

from enum import Enum


class StopReason(str, Enum):
    """The reason a ``query()`` generator stopped producing events."""

    end_turn = "end_turn"
    max_turns = "max_turns"
    cancelled = "cancelled"
    error = "error"
    hook_blocked = "hook_blocked"
    budget_exceeded = "budget_exceeded"
