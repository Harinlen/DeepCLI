"""Result for current session usage reporting."""

from __future__ import annotations

from pydantic import BaseModel


class TokenUsageSummary(BaseModel):
    """Token counters persisted for a session."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


class ContextUsageSection(BaseModel):
    """One visible section in the context usage panel."""

    id: str
    label: str
    tokens: int
    percent: float


class ContextUsageSummary(BaseModel):
    """Best-known context-window state for the latest completed turn."""

    total_tokens: int = 0
    context_window: int | None = None
    percent: float = 0.0
    sections: list[ContextUsageSection] = []


class HistoryUsageSummary(BaseModel):
    """Conversation history counters derived from session events."""

    messages: int = 0
    turns: int = 0
    tool_calls: int = 0
    compactions: int = 0
    queued_turns: int = 0
    in_flight: bool = False
    last_run_at: str | None = None
    last_duration_ms: int | None = None


class MemoryUsageSummary(BaseModel):
    """Memory counters available to the usage panel."""

    loaded: int = 0
    writable_scopes: int = 0


class EnvironmentUsageSummary(BaseModel):
    """Environment capabilities relevant to the current session."""

    lsp_servers: list[str] = []
    mcp_servers: list[str] = []


class GetUsageResult(BaseModel):
    """Usage dashboard data for a single session."""

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
