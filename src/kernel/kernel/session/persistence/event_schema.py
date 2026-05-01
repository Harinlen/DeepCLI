"""Pydantic models for every row written to the session event log.

The discriminated union ``SessionEvent`` at the bottom is the single
type used at the persistence boundary; all readers parse rows through
its ``TypeAdapter``.  ``_EventBase`` carries the fields shared by every
event (id, parent, timestamp, session id, cwd, kernel version, …).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from kernel import __version__

KERNEL_VERSION: str = __version__


class _EventBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    """``"ev_" + uuid4().hex`` — unique within the session file."""

    parent_id: str | None = None
    """Previous event's ``event_id``.  ``None`` for the first event.
    May point across files (sub-agent first event → main session event)."""

    timestamp: datetime
    """UTC time of writing."""

    session_id: str
    """UUID4 of the owning session (redundant but grep-friendly)."""

    agent_depth: int = 0
    """0 = main session; ≥ 1 = sub-agent file."""

    kernel_version: str = KERNEL_VERSION
    cwd: str
    """Session working directory at write time."""

    git_branch: str | None = None


class SessionCreatedEvent(_EventBase):
    """First event of every session — captures cwd, git branch, MCP config."""

    type: Literal["session_created"] = "session_created"
    mcp_servers: list[dict[str, Any]] = []


class SessionLoadedEvent(_EventBase):
    """Marker appended each time a client reattaches via ``session/load``."""

    type: Literal["session_loaded"] = "session_loaded"


class UserMessageEvent(_EventBase):
    """Written once per ``session/prompt`` call, before the turn starts."""

    type: Literal["user_message"] = "user_message"
    content: list[dict[str, Any]]
    """ContentBlock dicts; same shape as ACP content blocks."""
    request_id: str | int | None = None
    client_turn_id: str | None = None


class AgentMessageEvent(_EventBase):
    """Accumulated text output for one turn (not streaming chunks)."""

    type: Literal["agent_message"] = "agent_message"
    content: list[dict[str, Any]]


class AgentThoughtEvent(_EventBase):
    """Accumulated reasoning / extended-thinking output for one turn."""

    type: Literal["agent_thought"] = "agent_thought"
    content: list[dict[str, Any]]


class PlanEvent(_EventBase):
    """Plan tool output — the structured task list shown in the side panel."""

    type: Literal["plan"] = "plan"
    entries: list[dict[str, Any]]


class ToolCallEvent(_EventBase):
    """Emitted when a tool call begins."""

    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    title: str
    kind: str
    raw_input: str | None = None


class ToolCallUpdateEvent(_EventBase):
    """Status change or result for an in-progress tool call.

    ``content`` may contain a ``{"type": "spilled", ...}`` block when
    the result exceeded ``SessionFlags.tool_result_inline_limit``.
    SessionManager restores the full content on replay.
    """

    type: Literal["tool_call_update"] = "tool_call_update"
    tool_call_id: str
    status: str  # pending | in_progress | completed | failed
    content: list[dict[str, Any]] | None = None
    locations: list[dict[str, Any]] | None = None


class SubAgentSpawnedEvent(_EventBase):
    """Recorded when the orchestrator forks a sub-agent (Agent tool, …)."""

    type: Literal["sub_agent_spawned"] = "sub_agent_spawned"
    agent_id: str
    agent_type: str
    description: str


class SubAgentCompletedEvent(_EventBase):
    """Closes one ``SubAgentSpawnedEvent`` with its stop reason and duration."""

    type: Literal["sub_agent_completed"] = "sub_agent_completed"
    agent_id: str
    stop_reason: str
    duration_ms: int | None = None


class PermissionRequestEvent(_EventBase):
    """Tool call paused awaiting user approval — paired with one response."""

    type: Literal["permission_request"] = "permission_request"
    tool_call_id: str
    tool_name: str
    input_summary: str
    risk_level: str


class PermissionResponseEvent(_EventBase):
    """User's decision for a ``PermissionRequestEvent``."""

    type: Literal["permission_response"] = "permission_response"
    tool_call_id: str
    decision: str  # allow_once | allow_always | reject | cancelled


class ModeChangedEvent(_EventBase):
    """Session mode switched (default / plan / …)."""

    type: Literal["mode_changed"] = "mode_changed"
    mode_id: str
    from_mode: str | None = None


class ConfigOptionChangedEvent(_EventBase):
    """One config option changed; ``full_state`` snapshots the whole map."""

    type: Literal["config_option_changed"] = "config_option_changed"
    config_id: str
    """The key that changed.  Empty string when the event comes from an
    Orchestrator ``ConfigOptionChanged`` (full-state snapshot)."""
    value: str
    full_state: dict[str, Any]
    """Complete config snapshot at the time of the change."""


class AvailableCommandsChangedEvent(_EventBase):
    """Available command list changed (mode switch, skill load, …)."""

    type: Literal["available_commands_changed"] = "available_commands_changed"
    commands: list[dict[str, Any]]


class PlanUpdatedEvent(_EventBase):
    """Emitted when the plan file content changes (write or edit in plan mode).

    Stores plan content in the event log so it can be recovered even if
    the plan file on disk is lost (e.g. remote/ephemeral environments).
    """

    type: Literal["plan_updated"] = "plan_updated"
    plan_file_path: str
    content: str


class SessionInfoChangedEvent(_EventBase):
    """Title (and future session-level metadata) changed."""

    type: Literal["session_info_changed"] = "session_info_changed"
    title: str | None = None


class TurnStartedEvent(_EventBase):
    """Marks the start of one prompt turn — paired with ``TurnCompleted``."""

    type: Literal["turn_started"] = "turn_started"
    request_id: str | int | None = None
    client_turn_id: str | None = None
    queue_position: int = 0


class TurnCompletedEvent(_EventBase):
    """Closes a turn with stop reason, duration, and token deltas."""

    type: Literal["turn_completed"] = "turn_completed"
    request_id: str | int | None = None
    client_turn_id: str | None = None
    stop_reason: str
    duration_ms: int | None = None
    input_tokens: int = 0
    """Total LLM input tokens consumed during this turn."""
    output_tokens: int = 0
    """Total LLM output tokens generated during this turn."""


class TurnCancelledEvent(_EventBase):
    """Recorded when a turn was cancelled before reaching ``TurnCompleted``."""

    type: Literal["turn_cancelled"] = "turn_cancelled"
    request_id: str | int | None = None
    client_turn_id: str | None = None


class ConversationMessageEvent(_EventBase):
    """One Message object from ConversationHistory, serialized for resume.

    Written alongside UI events (AgentMessageEvent, ToolCallEvent, etc.)
    but serves a different purpose: UI events drive client display;
    this event drives orchestrator history reconstruction.
    """

    type: Literal["conversation_message"] = "conversation_message"
    schema_version: int = 1
    message: dict[str, Any]


class ConversationSnapshotEvent(_EventBase):
    """Full history snapshot after compaction.

    Replaces all prior ``ConversationMessageEvent`` entries on resume.
    """

    type: Literal["conversation_snapshot"] = "conversation_snapshot"
    schema_version: int = 1
    messages: list[dict[str, Any]]


SessionEvent = Annotated[
    Union[
        SessionCreatedEvent,
        SessionLoadedEvent,
        UserMessageEvent,
        AgentMessageEvent,
        AgentThoughtEvent,
        PlanEvent,
        ToolCallEvent,
        ToolCallUpdateEvent,
        SubAgentSpawnedEvent,
        SubAgentCompletedEvent,
        PermissionRequestEvent,
        PermissionResponseEvent,
        ModeChangedEvent,
        ConfigOptionChangedEvent,
        AvailableCommandsChangedEvent,
        SessionInfoChangedEvent,
        TurnStartedEvent,
        TurnCompletedEvent,
        TurnCancelledEvent,
        ConversationMessageEvent,
        ConversationSnapshotEvent,
    ],
    Field(discriminator="type"),
]
"""Discriminated union of every event type stored in a session JSONL file."""
