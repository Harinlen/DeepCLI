"""Configuration schema — re-export hub for backward compatibility.

Two-layer design (D7): users write partial YAML (SourceConfig), the
system merges defaults to produce RuntimeConfig (all fields required,
no None checks).

Source models live in :mod:`source_schema`, runtime models in
:mod:`runtime_schema`.  This module re-exports everything so
existing ``from daemon.config.schema import ...`` imports keep working.
"""

from __future__ import annotations

# Source configs (user YAML — all fields optional)
from daemon.config.source_schema import (
    AgentSourceConfig,
    BashToolSourceConfig,
    DaemonSourceConfig,
    HookSourceConfig,
    McpServerSourceConfig,
    MemoryAutoExtractSourceConfig,
    MemoryHotCacheSourceConfig,
    MemoryRelevanceSourceConfig,
    MemorySourceConfig,
    PermissionsSourceConfig,
    ProviderSourceConfig,
    SessionsSourceConfig,
    SkillsSourceConfig,
    SourceConfig,
    ToolsSourceConfig,
    WebSearchSourceConfig,
)

# Runtime configs (resolved — all fields guaranteed present)
from daemon.config.runtime_schema import (
    AgentRuntimeConfig,
    BashToolRuntimeConfig,
    DaemonRuntimeConfig,
    HookRuntimeConfig,
    McpServerRuntimeConfig,
    MemoryAutoExtractRuntimeConfig,
    MemoryHotCacheRuntimeConfig,
    MemoryRelevanceRuntimeConfig,
    MemoryRuntimeConfig,
    PermissionsRuntimeConfig,
    ProviderRuntimeConfig,
    RuntimeConfig,
    SessionsRuntimeConfig,
    SkillsRuntimeConfig,
    ToolsRuntimeConfig,
)

__all__ = [
    # Source
    "AgentSourceConfig",
    "BashToolSourceConfig",
    "DaemonSourceConfig",
    "HookSourceConfig",
    "McpServerSourceConfig",
    "MemoryAutoExtractSourceConfig",
    "MemoryHotCacheSourceConfig",
    "MemoryRelevanceSourceConfig",
    "MemorySourceConfig",
    "PermissionsSourceConfig",
    "ProviderSourceConfig",
    "SessionsSourceConfig",
    "SkillsSourceConfig",
    "SourceConfig",
    "ToolsSourceConfig",
    "WebSearchSourceConfig",
    # Runtime
    "AgentRuntimeConfig",
    "BashToolRuntimeConfig",
    "DaemonRuntimeConfig",
    "HookRuntimeConfig",
    "McpServerRuntimeConfig",
    "MemoryAutoExtractRuntimeConfig",
    "MemoryHotCacheRuntimeConfig",
    "MemoryRelevanceRuntimeConfig",
    "MemoryRuntimeConfig",
    "PermissionsRuntimeConfig",
    "ProviderRuntimeConfig",
    "RuntimeConfig",
    "SessionsRuntimeConfig",
    "SkillsRuntimeConfig",
    "ToolsRuntimeConfig",
]
