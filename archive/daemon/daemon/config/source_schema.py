"""Source configuration models — what the user writes in ``config.yaml``.

All fields are optional.  The system merges these with defaults
(see :mod:`daemon.config.defaults`) to produce the fully-resolved
:mod:`daemon.config.runtime_schema` types.
"""

from __future__ import annotations

from pydantic import BaseModel


class ProviderSourceConfig(BaseModel):
    """User-specified provider settings — all optional."""

    type: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    context_window: int | None = None
    thinking: str | int | None = None
    prompt_caching: bool | None = None
    aws_secret_key: str | None = None
    aws_region: str | None = None


class DaemonSourceConfig(BaseModel):
    """User-specified daemon network settings."""

    host: str | None = None
    port: int | None = None


class BashToolSourceConfig(BaseModel):
    """User-specified bash tool settings."""

    timeout: int | None = None  # milliseconds


class WebSearchSourceConfig(BaseModel):
    """User-specified web_search backend selection."""

    backend: str | None = None  # "brave" | "google" | "duckduckgo" | None = auto


class ToolsSourceConfig(BaseModel):
    """User-specified tool overrides."""

    bash: BashToolSourceConfig | None = None
    max_result_chars: int | None = None
    web_search: WebSearchSourceConfig | None = None


class SkillsSourceConfig(BaseModel):
    """User-specified skill settings."""

    disabled: list[str] | None = None


class HookSourceConfig(BaseModel):
    """A single hook definition from user config.

    Fields vary by ``type``:
    - ``command``: uses ``command``, optional ``timeout``, ``async_``
    - ``prompt``: uses ``prompt_text``, optional ``model``
    - ``http``: uses ``url``, optional ``headers``, ``body``
    """

    event: str
    type: str
    if_: str | None = None

    command: str | None = None
    timeout: int | None = None
    async_: bool | None = None

    prompt_text: str | None = None
    model: str | None = None

    url: str | None = None
    headers: dict[str, str] | None = None
    body: str | None = None

    model_config = {"populate_by_name": True}


class McpServerSourceConfig(BaseModel):
    """A single MCP server definition from user config."""

    type: str = "stdio"
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None


class AgentSourceConfig(BaseModel):
    """User-specified sub-agent settings (Phase 5.2)."""

    max_depth: int | None = None
    timeout_seconds: int | None = None
    max_concurrent: int | None = None


class PermissionsSourceConfig(BaseModel):
    """User-specified permission settings (all optional)."""

    mode: str | None = None


class SessionsSourceConfig(BaseModel):
    """User-specified session management settings."""

    max_age_days: int | None = None
    max_count: int | None = None
    max_file_mb: int | None = None
    cancelled_tool_policy: str | None = None


class MemoryAutoExtractSourceConfig(BaseModel):
    """User-specified memory auto-extract settings."""

    enabled: bool | None = None
    turn_interval: int | None = None
    max_new_memories: int | None = None
    extract_window: int | None = None
    min_messages: int | None = None
    timeout: int | None = None
    drain_timeout: int | None = None


class MemoryRelevanceSourceConfig(BaseModel):
    """User-specified memory relevance ranking settings."""

    enabled: bool | None = None
    threshold: int | None = None
    top_k: int | None = None
    timeout: int | None = None


class MemoryHotCacheSourceConfig(BaseModel):
    """User-specified memory hot cache settings."""

    enabled: bool | None = None
    top_n: int | None = None
    persist: bool | None = None


class MemorySourceConfig(BaseModel):
    """User-specified memory settings (Phase 5.7)."""

    auto_extract: MemoryAutoExtractSourceConfig | None = None
    relevance: MemoryRelevanceSourceConfig | None = None
    hot_cache: MemoryHotCacheSourceConfig | None = None


class SourceConfig(BaseModel):
    """What the user writes in config.yaml — everything optional."""

    provider: dict[str, ProviderSourceConfig | str] | None = None
    daemon: DaemonSourceConfig | None = None
    tools: ToolsSourceConfig | None = None
    skills: SkillsSourceConfig | None = None
    hooks: list[HookSourceConfig] | None = None
    mcp_servers: dict[str, McpServerSourceConfig] | None = None
    sessions: SessionsSourceConfig | None = None
    permissions: PermissionsSourceConfig | None = None
    agent: AgentSourceConfig | None = None
    memory: MemorySourceConfig | None = None
