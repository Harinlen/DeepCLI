"""Runtime configuration models — fully resolved, no ``None`` checks needed.

Produced by :func:`daemon.config.defaults.apply_defaults` from the
user's :class:`SourceConfig`.  Every field is guaranteed present.
"""

from __future__ import annotations

from pydantic import BaseModel


class ProviderRuntimeConfig(BaseModel):
    """Resolved provider config — every field guaranteed present."""

    type: str
    base_url: str
    model: str
    api_key: str
    context_window: int | None = None
    thinking: str | int | None = None
    prompt_caching: bool | None = None
    aws_secret_key: str | None = None
    aws_region: str | None = None


class DaemonRuntimeConfig(BaseModel):
    """Resolved daemon network config."""

    host: str
    port: int


class BashToolRuntimeConfig(BaseModel):
    """Resolved bash tool config."""

    timeout: int  # milliseconds


class ToolsRuntimeConfig(BaseModel):
    """Resolved tool configs."""

    bash: BashToolRuntimeConfig
    max_result_chars: int | None = None
    web_search_backend: str | None = None


class SkillsRuntimeConfig(BaseModel):
    """Resolved skill config."""

    disabled: list[str]


class HookRuntimeConfig(BaseModel):
    """A single resolved hook definition."""

    event: str
    type: str
    if_: str | None = None

    command: str | None = None
    timeout: int = 30
    async_: bool = False

    prompt_text: str | None = None
    model: str | None = None

    url: str | None = None
    headers: dict[str, str] | None = None
    body: str | None = None


class McpServerRuntimeConfig(BaseModel):
    """A single resolved MCP server definition."""

    type: str
    command: str
    args: list[str]
    env: dict[str, str]


class PermissionsRuntimeConfig(BaseModel):
    """Resolved permission config."""

    mode: str


class SessionsRuntimeConfig(BaseModel):
    """Resolved session management config."""

    max_age_days: int
    max_count: int
    max_file_mb: int
    cancelled_tool_policy: str = "acknowledge"


class AgentRuntimeConfig(BaseModel):
    """Resolved sub-agent config (Phase 5.2)."""

    max_depth: int = 3
    timeout_seconds: int = 300
    max_concurrent: int = 5


class MemoryAutoExtractRuntimeConfig(BaseModel):
    """Resolved memory auto-extract config."""

    enabled: bool = True
    turn_interval: int = 5
    max_new_memories: int = 3
    extract_window: int = 40
    min_messages: int = 4
    timeout: int = 60
    drain_timeout: int = 60


class MemoryRelevanceRuntimeConfig(BaseModel):
    """Resolved memory relevance ranking config."""

    enabled: bool = True
    threshold: int = 30
    top_k: int = 5
    timeout: int = 10


class MemoryHotCacheRuntimeConfig(BaseModel):
    """Resolved memory hot cache config."""

    enabled: bool = True
    top_n: int = 10
    persist: bool = True


class MemoryRuntimeConfig(BaseModel):
    """Resolved memory config (Phase 5.7)."""

    auto_extract: MemoryAutoExtractRuntimeConfig = MemoryAutoExtractRuntimeConfig()
    relevance: MemoryRelevanceRuntimeConfig = MemoryRelevanceRuntimeConfig()
    hot_cache: MemoryHotCacheRuntimeConfig = MemoryHotCacheRuntimeConfig()


class RuntimeConfig(BaseModel):
    """Fully resolved config — safe to use without None checks."""

    default_provider: str
    providers: dict[str, ProviderRuntimeConfig]
    daemon: DaemonRuntimeConfig
    tools: ToolsRuntimeConfig
    skills: SkillsRuntimeConfig
    hooks: list[HookRuntimeConfig]
    mcp_servers: dict[str, McpServerRuntimeConfig]
    sessions: SessionsRuntimeConfig
    permissions: PermissionsRuntimeConfig
    agent: AgentRuntimeConfig = AgentRuntimeConfig()
    memory: MemoryRuntimeConfig = MemoryRuntimeConfig()
