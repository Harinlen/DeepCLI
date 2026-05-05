"""ACP session method wire-format schemas."""

from __future__ import annotations

from typing import Any
from typing import Literal

from kernel.protocol.acp.schemas.base import AcpModel
from kernel.protocol.acp.schemas.content import AcpContentBlock
from kernel.protocol.acp.schemas.enums import AcpStopReason


class AcpMcpServer(AcpModel):
    name: str
    command: str | None = None
    args: list[str] = []
    env: list[dict[str, str]] = []
    type: str | None = None
    url: str | None = None
    headers: list[dict[str, str]] = []


class AcpSessionInfo(AcpModel):
    session_id: str
    cwd: str
    updated_at: str
    title: str | None = None
    archived_at: str | None = None
    title_source: Literal["auto", "user"] | None = None
    meta: dict[str, Any] | None = None


# session/new


class NewSessionRequest(AcpModel):
    cwd: str
    mcp_servers: list[AcpMcpServer] = []
    meta: dict[str, Any] | None = None


class NewSessionResponse(AcpModel):
    session_id: str
    config_options: list[dict] | None = None
    modes: dict | None = None
    meta: dict[str, Any] | None = None


# session/load


class LoadSessionRequest(AcpModel):
    session_id: str
    cwd: str
    mcp_servers: list[AcpMcpServer] = []
    meta: dict[str, Any] | None = None


class LoadSessionResponse(AcpModel):
    config_options: list[dict] | None = None
    modes: dict | None = None
    meta: dict[str, Any] | None = None


# session/resume


class ResumeSessionRequest(AcpModel):
    session_id: str
    cwd: str | None = None
    meta: dict[str, Any] | None = None


class ResumeSessionResponse(AcpModel):
    config_options: list[dict] | None = None
    modes: dict | None = None
    meta: dict[str, Any] | None = None


# session/close


class CloseSessionRequest(AcpModel):
    session_id: str
    meta: dict[str, Any] | None = None


class CloseSessionResponse(AcpModel):
    meta: dict[str, Any] | None = None


# session/list


class ListSessionsRequest(AcpModel):
    cursor: str | None = None
    cwd: str | None = None
    include_archived: bool = False
    archived_only: bool = False
    meta: dict[str, Any] | None = None


class ListSessionsResponse(AcpModel):
    sessions: list[AcpSessionInfo]
    next_cursor: str | None = None
    meta: dict[str, Any] | None = None


# session/prompt


class PromptRequest(AcpModel):
    session_id: str
    prompt: list[AcpContentBlock]
    max_turns: int = 0
    meta: dict[str, Any] | None = None


class PromptResponse(AcpModel):
    stop_reason: AcpStopReason
    meta: dict[str, Any] | None = None


# session/execute_shell


class ExecuteShellRequest(AcpModel):
    session_id: str
    command: str
    exclude_from_context: bool = False
    shell: str = "auto"
    meta: dict[str, Any] | None = None


class ExecuteShellResponse(AcpModel):
    exit_code: int
    cancelled: bool = False
    meta: dict[str, Any] | None = None


# session/execute_python


class ExecutePythonRequest(AcpModel):
    session_id: str
    code: str
    exclude_from_context: bool = False
    meta: dict[str, Any] | None = None


class ExecutePythonResponse(AcpModel):
    exit_code: int
    cancelled: bool = False
    meta: dict[str, Any] | None = None


# session/cancel_execution


class CancelExecutionRequest(AcpModel):
    session_id: str
    kind: str = "any"
    meta: dict[str, Any] | None = None


class CancelExecutionResponse(AcpModel):
    meta: dict[str, Any] | None = None


# session/cancel (notification)


class CancelNotification(AcpModel):
    session_id: str
    meta: dict[str, Any] | None = None


class CancelRequestNotification(AcpModel):
    request_id: str | int
    meta: dict[str, Any] | None = None


# session/set_mode


class SetSessionModeRequest(AcpModel):
    session_id: str
    mode_id: str
    meta: dict[str, Any] | None = None


class SetSessionModeResponse(AcpModel):
    meta: dict[str, Any] | None = None


# session/set_config_option


class SetSessionConfigOptionRequest(AcpModel):
    session_id: str
    config_id: str
    value: str
    meta: dict[str, Any] | None = None


class SetSessionConfigOptionResponse(AcpModel):
    config_options: list[dict]
    meta: dict[str, Any] | None = None


# session/rename


class RenameSessionRequest(AcpModel):
    session_id: str
    title: str
    meta: dict[str, Any] | None = None


class RenameSessionResponse(AcpModel):
    session: AcpSessionInfo
    meta: dict[str, Any] | None = None


# session/archive


class ArchiveSessionRequest(AcpModel):
    session_id: str
    archived: bool = True
    meta: dict[str, Any] | None = None


class ArchiveSessionResponse(AcpModel):
    session: AcpSessionInfo
    meta: dict[str, Any] | None = None


# session/delete


class DeleteSessionRequest(AcpModel):
    session_id: str
    force: bool = False
    meta: dict[str, Any] | None = None


class DeleteSessionResponse(AcpModel):
    deleted: bool
    meta: dict[str, Any] | None = None


# _mustang.agent/session/get_usage


class GetUsageRequest(AcpModel):
    session_id: str
    meta: dict[str, Any] | None = None


class TokenUsageSummary(AcpModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


class ContextUsageSection(AcpModel):
    id: str
    label: str
    tokens: int
    percent: float


class ContextUsageSummary(AcpModel):
    total_tokens: int = 0
    context_window: int | None = None
    percent: float = 0.0
    sections: list[ContextUsageSection] = []


class HistoryUsageSummary(AcpModel):
    messages: int = 0
    turns: int = 0
    tool_calls: int = 0
    compactions: int = 0
    queued_turns: int = 0
    in_flight: bool = False
    last_run_at: str | None = None
    last_duration_ms: int | None = None


class MemoryUsageSummary(AcpModel):
    loaded: int = 0
    writable_scopes: int = 0


class EnvironmentUsageSummary(AcpModel):
    lsp_servers: list[str] = []
    mcp_servers: list[str] = []


class GetUsageResponse(AcpModel):
    session_id: str
    title: str | None = None
    cwd: str
    created_at: str | None = None
    updated_at: str | None = None
    model: str | None = None
    kernel_version: str
    tokens: TokenUsageSummary
    context: ContextUsageSummary
    history: HistoryUsageSummary
    memory: MemoryUsageSummary
    environment: EnvironmentUsageSummary
    cost_usd: float | None = None
    cost_note: str | None = None
    meta: dict[str, Any] | None = None
