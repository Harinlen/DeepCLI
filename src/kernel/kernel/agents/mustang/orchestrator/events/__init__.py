"""Orchestrator event dataclasses and union type."""

from __future__ import annotations

from typing import Union

from kernel.agents.mustang.orchestrator.events.agents import SubAgentEnd, SubAgentStart
from kernel.agents.mustang.orchestrator.events.housekeeping import (
    CancelledEvent,
    CompactionEvent,
    HistoryAppend,
    HistorySnapshot,
    QueryError,
    UserPromptBlocked,
)
from kernel.agents.mustang.orchestrator.events.session import (
    AvailableCommandsChanged,
    ConfigOptionChanged,
    ModeChanged,
    PlanUpdate,
    SessionInfoChanged,
)
from kernel.agents.mustang.orchestrator.events.streaming import TextDelta, ThoughtDelta
from kernel.agents.mustang.orchestrator.events.tools import (
    ToolCallDiff,
    ToolCallError,
    ToolCallLocations,
    ToolCallProgress,
    ToolCallResult,
    ToolCallStart,
)

OrchestratorEvent = Union[
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    ToolCallProgress,
    ToolCallResult,
    ToolCallError,
    ToolCallDiff,
    ToolCallLocations,
    PlanUpdate,
    ModeChanged,
    ConfigOptionChanged,
    SessionInfoChanged,
    AvailableCommandsChanged,
    SubAgentStart,
    SubAgentEnd,
    CompactionEvent,
    QueryError,
    UserPromptBlocked,
    CancelledEvent,
    HistoryAppend,
    HistorySnapshot,
]

__all__ = [
    "TextDelta",
    "ThoughtDelta",
    "ToolCallStart",
    "ToolCallProgress",
    "ToolCallResult",
    "ToolCallError",
    "ToolCallDiff",
    "ToolCallLocations",
    "PlanUpdate",
    "ModeChanged",
    "ConfigOptionChanged",
    "SessionInfoChanged",
    "AvailableCommandsChanged",
    "SubAgentStart",
    "SubAgentEnd",
    "CompactionEvent",
    "QueryError",
    "UserPromptBlocked",
    "CancelledEvent",
    "HistoryAppend",
    "HistorySnapshot",
    "OrchestratorEvent",
]
