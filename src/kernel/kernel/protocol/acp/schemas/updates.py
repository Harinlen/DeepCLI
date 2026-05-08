"""ACP ``session/update`` notification variants (10 total)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import Field

from kernel.protocol.acp.schemas.base import AcpModel
from kernel.protocol.acp.schemas.content import AcpContentBlock
from kernel.protocol.acp.schemas.enums import (
    AcpPlanEntryPriority,
    AcpPlanEntryStatus,
    AcpToolCallStatus,
    AcpToolKind,
)


class AgentMessageChunk(AcpModel):
    session_update: Literal["agent_message_chunk"] = "agent_message_chunk"
    content: AcpContentBlock
    meta: dict[str, Any] | None = None


class AgentThoughtChunk(AcpModel):
    session_update: Literal["agent_thought_chunk"] = "agent_thought_chunk"
    content: AcpContentBlock
    meta: dict[str, Any] | None = None


class UserMessageChunk(AcpModel):
    """Used only during ``session/load`` history replay."""

    session_update: Literal["user_message_chunk"] = "user_message_chunk"
    content: AcpContentBlock
    meta: dict[str, Any] | None = None


class ToolCallLocation(AcpModel):
    path: str
    line: int | None = None


class ToolCallStart(AcpModel):
    session_update: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    title: str
    kind: AcpToolKind = "other"
    status: Literal["pending"] = "pending"
    raw_input: str | None = None
    meta: dict[str, Any] | None = None


class ToolCallUpdateNotification(AcpModel):
    session_update: Literal["tool_call_update"] = "tool_call_update"
    tool_call_id: str
    status: AcpToolCallStatus
    content: list[dict] | None = None
    locations: list[ToolCallLocation] | None = None
    meta: dict[str, Any] | None = None


class PlanEntry(AcpModel):
    content: str
    priority: AcpPlanEntryPriority = "medium"
    status: AcpPlanEntryStatus = "pending"


class PlanUpdate(AcpModel):
    session_update: Literal["plan"] = "plan"
    entries: list[PlanEntry]
    meta: dict[str, Any] | None = None


class CurrentModeUpdate(AcpModel):
    session_update: Literal["current_mode_update"] = "current_mode_update"
    mode_id: str
    meta: dict[str, Any] | None = None


class ConfigOptionUpdate(AcpModel):
    session_update: Literal["config_option_update"] = "config_option_update"
    config_options: list[dict]
    meta: dict[str, Any] | None = None


class SessionInfoUpdate(AcpModel):
    session_update: Literal["session_info_update"] = "session_info_update"
    title: str | None = None
    updated_at: str | None = None
    meta: dict[str, Any] | None = None


class AvailableCommandsUpdate(AcpModel):
    session_update: Literal["available_commands_update"] = "available_commands_update"
    available_commands: list[dict]
    meta: dict[str, Any] | None = None


class UsageUpdate(AcpModel):
    session_update: Literal["usage_update"] = "usage_update"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    used: int = 0
    size: int | None = None
    duration_ms: int | None = None
    meta: dict[str, Any] | None = None


class MustangExecution(AcpModel):
    phase: Literal["start", "chunk", "end"]
    kind: Literal["shell", "python"]
    execution_id: str
    stream: Literal["stdout", "stderr"] | None = None
    text: str | None = None
    exit_code: int | None = None
    cancelled: bool = False
    input: str | None = None
    shell: str | None = None
    exclude_from_context: bool = False


class MustangExecutionUpdateNotification(AcpModel):
    session_id: str
    execution: MustangExecution
    meta: dict[str, Any] | None = None


SessionUpdate = Annotated[
    Union[
        AgentMessageChunk,
        AgentThoughtChunk,
        UserMessageChunk,
        ToolCallStart,
        ToolCallUpdateNotification,
        PlanUpdate,
        CurrentModeUpdate,
        ConfigOptionUpdate,
        SessionInfoUpdate,
        AvailableCommandsUpdate,
        UsageUpdate,
    ],
    Field(discriminator="session_update"),
]


class SessionUpdateNotification(AcpModel):
    """Params of the ``session/update`` notification."""

    session_id: str
    update: SessionUpdate
    meta: dict[str, Any] | None = None
