"""Compatibility exports and serde for session persistence events."""

from __future__ import annotations

from pydantic import TypeAdapter

from kernel.session.persistence.event_schema import (
    KERNEL_VERSION,
    AgentMessageEvent,
    AgentThoughtEvent,
    AvailableCommandsChangedEvent,
    ConfigOptionChangedEvent,
    ConversationMessageEvent,
    ConversationSnapshotEvent,
    ModeChangedEvent,
    PermissionRequestEvent,
    PermissionResponseEvent,
    PlanEvent,
    PlanUpdatedEvent,
    SessionCreatedEvent,
    SessionEvent,
    SessionInfoChangedEvent,
    SessionLoadedEvent,
    SubAgentCompletedEvent,
    SubAgentSpawnedEvent,
    ToolCallEvent,
    ToolCallUpdateEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    UserMessageEvent,
    _EventBase,
)

__all__ = [
    "KERNEL_VERSION",
    "AgentMessageEvent",
    "AgentThoughtEvent",
    "AvailableCommandsChangedEvent",
    "ConfigOptionChangedEvent",
    "ConversationMessageEvent",
    "ConversationSnapshotEvent",
    "ModeChangedEvent",
    "PermissionRequestEvent",
    "PermissionResponseEvent",
    "PlanEvent",
    "PlanUpdatedEvent",
    "SessionCreatedEvent",
    "SessionEvent",
    "SessionInfoChangedEvent",
    "SessionLoadedEvent",
    "SubAgentCompletedEvent",
    "SubAgentSpawnedEvent",
    "ToolCallEvent",
    "ToolCallUpdateEvent",
    "TurnCancelledEvent",
    "TurnCompletedEvent",
    "TurnStartedEvent",
    "UserMessageEvent",
    "parse_event",
    "serialize_event",
]

_ADAPTER: TypeAdapter[SessionEvent] = TypeAdapter(SessionEvent)


def parse_event(line: str) -> SessionEvent:
    """Deserialise one stored event row into a typed ``SessionEvent``."""
    return _ADAPTER.validate_json(line)


def serialize_event(event: _EventBase) -> str:
    """Serialise an event to JSON with a trailing newline."""
    return event.model_dump_json() + "\n"
