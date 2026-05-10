"""Orchestrator public package API."""

from __future__ import annotations

from kernel.agents.mustang.orchestrator.api import Orchestrator
from kernel.agents.mustang.orchestrator.config import OrchestratorConfig, OrchestratorConfigPatch
from kernel.agents.mustang.orchestrator.deps import LLMProvider, OrchestratorDeps
from kernel.agents.mustang.orchestrator.events import (
    AvailableCommandsChanged,
    CancelledEvent,
    CompactionEvent,
    ConfigOptionChanged,
    HistoryAppend,
    HistorySnapshot,
    ModeChanged,
    OrchestratorEvent,
    PlanUpdate,
    QueryError,
    SessionInfoChanged,
    SubAgentEnd,
    SubAgentStart,
    TextDelta,
    ThoughtDelta,
    ToolCallDiff,
    ToolCallError,
    ToolCallLocations,
    ToolCallProgress,
    ToolCallResult,
    ToolCallStart,
    UserPromptBlocked,
)
from kernel.agents.mustang.orchestrator.permissions import (
    PermissionCallback,
    PermissionRequest,
    PermissionRequestOption,
    PermissionResponse,
)
from kernel.agents.mustang.orchestrator.stop import StopReason
from kernel.agents.mustang.orchestrator.tool_kinds import ToolKind

__all__ = [
    "Orchestrator",
    "OrchestratorConfig",
    "OrchestratorConfigPatch",
    "OrchestratorDeps",
    "LLMProvider",
    "OrchestratorEvent",
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
    "StopReason",
    "ToolKind",
    "PermissionRequest",
    "PermissionRequestOption",
    "PermissionResponse",
    "PermissionCallback",
]
