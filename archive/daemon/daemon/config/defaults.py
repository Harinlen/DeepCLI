"""Default configuration values for Mustang daemon."""

from __future__ import annotations

from daemon.config.schema import (
    BashToolRuntimeConfig,
    DaemonRuntimeConfig,
    HookRuntimeConfig,
    HookSourceConfig,
    McpServerRuntimeConfig,
    McpServerSourceConfig,
    MemoryAutoExtractRuntimeConfig,
    MemoryHotCacheRuntimeConfig,
    MemoryRelevanceRuntimeConfig,
    MemoryRuntimeConfig,
    PermissionsRuntimeConfig,
    ProviderRuntimeConfig,
    RuntimeConfig,
    SessionsRuntimeConfig,
    SkillsRuntimeConfig,
    SourceConfig,
    ToolsRuntimeConfig,
)

# Default provider preset — local llama.cpp / Ollama / vLLM
DEFAULT_PROVIDER_NAME = "local"
DEFAULT_PROVIDER_TYPE = "openai_compatible"
DEFAULT_PROVIDER = ProviderRuntimeConfig(
    type=DEFAULT_PROVIDER_TYPE,
    base_url="http://127.0.0.1:8080/v1",
    model="qwen3.5",
    api_key="no-key",
)

DEFAULT_DAEMON = DaemonRuntimeConfig(
    host="127.0.0.1",
    port=7777,
)

DEFAULT_BASH_TIMEOUT = 120_000  # 2 minutes
DEFAULT_HOOK_TIMEOUT = 30  # seconds

# Session cleanup defaults (matching Claude Code's 30-day policy)
DEFAULT_SESSION_MAX_AGE_DAYS = 30
DEFAULT_SESSION_MAX_COUNT = 200
DEFAULT_SESSION_MAX_FILE_MB = 50

# Cancelled-tool history policy (Phase 4.X crash recovery).  Governs
# what the LLM sees for synthetic cancellation entries on resume.
DEFAULT_CANCELLED_TOOL_POLICY = "acknowledge"
_VALID_CANCELLED_TOOL_POLICIES: frozenset[str] = frozenset({"acknowledge", "hide", "verbatim"})


def _coerce_cancelled_tool_policy(raw: str | None) -> str:
    """Normalize a user-supplied cancelled_tool_policy value.

    Unknown values fall back to the default so a typo never blocks
    daemon startup.  Recognised values pass through (case-insensitive).
    """
    if not raw:
        return DEFAULT_CANCELLED_TOOL_POLICY
    lowered = raw.strip().lower()
    if lowered in _VALID_CANCELLED_TOOL_POLICIES:
        return lowered
    return DEFAULT_CANCELLED_TOOL_POLICY


# Permission mode default — matches Claude Code's "default" mode.
DEFAULT_PERMISSION_MODE = "default"

# Valid mode enum values after backward-compat coercion.
_VALID_PERMISSION_MODES: frozenset[str] = frozenset({"default", "accept_edits", "plan", "bypass"})


def _coerce_permission_mode(raw: str | None) -> str:
    """Resolve a user-supplied mode string to the canonical enum value.

    Applies Phase 4.6 backward compatibility: Mustang MVP used the
    value ``"prompt"`` for the default permission mode; Claude Code
    (and Phase 4.6) uses ``"default"``.  Both are accepted here so
    existing ``config.yaml`` files continue to work.

    Unknown modes fall back to the default with no exception — the
    daemon must start even if the user typo'd the mode.
    """
    if not raw:
        return DEFAULT_PERMISSION_MODE
    lowered = raw.strip().lower()
    if lowered == "prompt":  # MVP alias
        return "default"
    if lowered in _VALID_PERMISSION_MODES:
        return lowered
    return DEFAULT_PERMISSION_MODE


def _resolve_hook(src: HookSourceConfig) -> HookRuntimeConfig:
    """Resolve a single hook source config to runtime config."""
    return HookRuntimeConfig(
        event=src.event,
        type=src.type,
        if_=src.if_,
        command=src.command,
        timeout=src.timeout if src.timeout is not None else DEFAULT_HOOK_TIMEOUT,
        async_=src.async_ if src.async_ is not None else False,
        prompt_text=src.prompt_text,
        model=src.model,
        url=src.url,
        headers=src.headers,
        body=src.body,
    )


def _resolve_mcp_server(
    src: McpServerSourceConfig,
) -> McpServerRuntimeConfig | None:
    """Resolve a single MCP server source config.

    Returns None if the server config is incomplete (missing command).
    """
    if not src.command:
        return None
    return McpServerRuntimeConfig(
        type=src.type,
        command=src.command,
        args=src.args if src.args is not None else [],
        env=src.env if src.env is not None else {},
    )


def apply_defaults(source: SourceConfig) -> RuntimeConfig:
    """Merge user-provided source config with defaults to produce RuntimeConfig.

    Args:
        source: Partial config from user YAML (may have None fields).

    Returns:
        Fully resolved RuntimeConfig with no None values.
    """

    # --- Providers ---
    providers: dict[str, ProviderRuntimeConfig] = {}
    default_provider = DEFAULT_PROVIDER_NAME

    if source.provider:
        # Extract the "default" key if present
        raw_default = source.provider.get("default")
        if isinstance(raw_default, str):
            default_provider = raw_default

        for name, val in source.provider.items():
            if name == "default":
                continue
            if isinstance(val, str):
                # Just a reference string, skip
                continue
            providers[name] = ProviderRuntimeConfig(
                type=val.type if val.type is not None else DEFAULT_PROVIDER_TYPE,
                base_url=val.base_url if val.base_url is not None else DEFAULT_PROVIDER.base_url,
                model=val.model if val.model is not None else DEFAULT_PROVIDER.model,
                api_key=val.api_key if val.api_key is not None else DEFAULT_PROVIDER.api_key,
                context_window=val.context_window,
                thinking=val.thinking,
                prompt_caching=val.prompt_caching,
                aws_secret_key=val.aws_secret_key,
                aws_region=val.aws_region,
            )

    # Ensure the default provider exists
    if default_provider not in providers:
        providers[default_provider] = DEFAULT_PROVIDER

    # --- Daemon ---
    daemon = DEFAULT_DAEMON
    if source.daemon:
        daemon = DaemonRuntimeConfig(
            host=source.daemon.host if source.daemon.host is not None else DEFAULT_DAEMON.host,
            port=source.daemon.port if source.daemon.port is not None else DEFAULT_DAEMON.port,
        )

    # --- Tools ---
    bash_timeout = DEFAULT_BASH_TIMEOUT
    max_result_chars: int | None = None
    web_search_backend: str | None = None
    if source.tools:
        if source.tools.bash and source.tools.bash.timeout:
            bash_timeout = source.tools.bash.timeout
        max_result_chars = source.tools.max_result_chars
        if source.tools.web_search:
            web_search_backend = source.tools.web_search.backend

    tools = ToolsRuntimeConfig(
        bash=BashToolRuntimeConfig(timeout=bash_timeout),
        max_result_chars=max_result_chars,
        web_search_backend=web_search_backend,
    )

    # --- Skills ---
    skills = SkillsRuntimeConfig(disabled=[])
    if source.skills and source.skills.disabled:
        skills = SkillsRuntimeConfig(disabled=source.skills.disabled)

    # --- Hooks ---
    hooks: list[HookRuntimeConfig] = []
    if source.hooks:
        hooks = [_resolve_hook(h) for h in source.hooks]

    # --- MCP Servers ---
    mcp_servers: dict[str, McpServerRuntimeConfig] = {}
    if source.mcp_servers:
        for name, srv in source.mcp_servers.items():
            resolved = _resolve_mcp_server(srv)
            if resolved:
                mcp_servers[name] = resolved

    # --- Sessions ---
    sessions = SessionsRuntimeConfig(
        max_age_days=DEFAULT_SESSION_MAX_AGE_DAYS,
        max_count=DEFAULT_SESSION_MAX_COUNT,
        max_file_mb=DEFAULT_SESSION_MAX_FILE_MB,
        cancelled_tool_policy=DEFAULT_CANCELLED_TOOL_POLICY,
    )
    if source.sessions:
        sessions = SessionsRuntimeConfig(
            max_age_days=source.sessions.max_age_days if source.sessions.max_age_days is not None else DEFAULT_SESSION_MAX_AGE_DAYS,
            max_count=source.sessions.max_count if source.sessions.max_count is not None else DEFAULT_SESSION_MAX_COUNT,
            max_file_mb=source.sessions.max_file_mb if source.sessions.max_file_mb is not None else DEFAULT_SESSION_MAX_FILE_MB,
            cancelled_tool_policy=_coerce_cancelled_tool_policy(
                source.sessions.cancelled_tool_policy
            ),
        )

    # --- Permissions ---
    perms_mode = DEFAULT_PERMISSION_MODE
    if source.permissions and source.permissions.mode:
        perms_mode = _coerce_permission_mode(source.permissions.mode)
    permissions = PermissionsRuntimeConfig(mode=perms_mode)

    # --- Agent ---
    from daemon.config.schema import AgentRuntimeConfig

    agent = AgentRuntimeConfig()
    if source.agent:
        agent = AgentRuntimeConfig(
            max_depth=source.agent.max_depth if source.agent.max_depth is not None else agent.max_depth,
            timeout_seconds=source.agent.timeout_seconds if source.agent.timeout_seconds is not None else agent.timeout_seconds,
            max_concurrent=source.agent.max_concurrent if source.agent.max_concurrent is not None else agent.max_concurrent,
        )

    # --- Memory (Phase 5.7) ---
    memory = MemoryRuntimeConfig()
    if source.memory:
        ae = source.memory.auto_extract
        rel = source.memory.relevance
        hc = source.memory.hot_cache
        def _v(src: object, field: str, default: object) -> object:
            """Return src.field if not None, else default."""
            val = getattr(src, field, None) if src else None
            return val if val is not None else default

        memory = MemoryRuntimeConfig(
            auto_extract=MemoryAutoExtractRuntimeConfig(
                enabled=_v(ae, "enabled", True),  # type: ignore[arg-type]
                turn_interval=_v(ae, "turn_interval", 5),  # type: ignore[arg-type]
                max_new_memories=_v(ae, "max_new_memories", 3),  # type: ignore[arg-type]
                extract_window=_v(ae, "extract_window", 40),  # type: ignore[arg-type]
                min_messages=_v(ae, "min_messages", 4),  # type: ignore[arg-type]
                timeout=_v(ae, "timeout", 60),  # type: ignore[arg-type]
                drain_timeout=_v(ae, "drain_timeout", 60),  # type: ignore[arg-type]
            ),
            relevance=MemoryRelevanceRuntimeConfig(
                enabled=_v(rel, "enabled", True),  # type: ignore[arg-type]
                threshold=_v(rel, "threshold", 30),  # type: ignore[arg-type]
                top_k=_v(rel, "top_k", 5),  # type: ignore[arg-type]
                timeout=_v(rel, "timeout", 10),  # type: ignore[arg-type]
            ),
            hot_cache=MemoryHotCacheRuntimeConfig(
                enabled=_v(hc, "enabled", True),  # type: ignore[arg-type]
                top_n=_v(hc, "top_n", 10),  # type: ignore[arg-type]
                persist=_v(hc, "persist", True),  # type: ignore[arg-type]
            ),
        )

    return RuntimeConfig(
        default_provider=default_provider,
        providers=providers,
        daemon=daemon,
        tools=tools,
        skills=skills,
        hooks=hooks,
        mcp_servers=mcp_servers,
        sessions=sessions,
        permissions=permissions,
        agent=agent,
        memory=memory,
    )
