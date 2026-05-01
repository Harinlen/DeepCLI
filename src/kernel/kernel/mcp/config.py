"""MCP server configuration — Pydantic schemas, .mcp.json compat, policy.

Mirrors Claude Code ``services/mcp/config.ts``:
- Server config types (stdio / sse / http / ws) as discriminated union
- ``.mcp.json`` loading + env-var expansion (CC project convention)
- Policy filtering (allowed / denied server lists)

Config sources (high → low priority):
1. ConfigManager three-layer (local > project > global)
2. ``<cwd>/.mcp.json`` (Claude Code convention, merged where no conflict)
"""

from __future__ import annotations

import orjson
import logging
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Server config types ─────────────────────────────────────────────


class StdioServerConfig(BaseModel):
    """Stdio transport — spawn a local child process.

    Attributes:
        command: Executable to run.
        args: Command-line arguments.
        env: Extra environment variables (``$VAR`` / ``${VAR}`` expanded).
    """

    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class SSEServerConfig(BaseModel):
    """SSE transport — connect to a remote HTTP SSE endpoint.

    Attributes:
        url: Base SSE endpoint URL.
        headers: Extra HTTP headers (auth tokens, etc.).
    """

    type: Literal["sse"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class HTTPServerConfig(BaseModel):
    """Streamable HTTP transport — the MCP spec's successor to SSE.

    Attributes:
        url: MCP HTTP endpoint URL.
        headers: Extra HTTP headers.
    """

    type: Literal["http"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class WebSocketServerConfig(BaseModel):
    """WebSocket transport — full-duplex remote connection.

    Attributes:
        url: WebSocket endpoint (``ws://`` or ``wss://``).
        headers: Extra HTTP headers sent during upgrade.
    """

    type: Literal["ws"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


ServerConfig = Annotated[
    StdioServerConfig | SSEServerConfig | HTTPServerConfig | WebSocketServerConfig,
    Field(discriminator="type"),
]
"""Discriminated union of all supported MCP server configs."""


# ── Top-level config section ────────────────────────────────────────


class MCPConfig(BaseModel):
    """ConfigManager section: ``file='mcp', section='servers'``.

    Attributes:
        servers: Name → config mapping.
    """

    servers: dict[str, ServerConfig] = Field(default_factory=dict)


# ── Policy config ───────────────────────────────────────────────────


class MCPPolicyConfig(BaseModel):
    """ConfigManager section: ``file='config', section='mcp_policy'``.

    Mirrors CC's ``allowedMcpServers`` / ``deniedMcpServers``.

    Attributes:
        allowed_servers: Allowlist.  ``None`` = allow all;
            empty list ``[]`` = deny all.
        denied_servers: Denylist.  Always takes precedence.
    """

    allowed_servers: list[str] | None = None
    denied_servers: list[str] = Field(default_factory=list)


# ── .mcp.json loading ───────────────────────────────────────────────


def load_mcp_json(path: Path) -> dict[str, ServerConfig]:
    """Load servers from a ``.mcp.json`` file (Claude Code convention).

    The file format::

        {
          "mcpServers": {
            "server-name": {
              "command": "npx",
              "args": ["-y", "@some/mcp-server"],
              "env": {"API_KEY": "${API_KEY}"}
            }
          }
        }

    Stdio servers may omit ``"type"`` (defaults to ``"stdio"``).
    Environment variables in ``command``, ``args``, and ``env`` values
    are expanded at transport level, not here.

    Args:
        path: Path to the ``.mcp.json`` file.

    Returns:
        Name → ServerConfig mapping.  Empty dict if file missing or
        unparseable.
    """
    if not path.is_file():
        return {}

    try:
        raw = orjson.loads(path.read_text(encoding="utf-8"))
    except (orjson.JSONDecodeError, OSError) as exc:
        logger.warning("load_mcp_json: failed to read %s: %s", path, exc)
        return {}

    mcp_servers = raw.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        logger.warning("load_mcp_json: 'mcpServers' is not a dict in %s", path)
        return {}

    result: dict[str, ServerConfig] = {}
    for name, entry in mcp_servers.items():
        if not isinstance(entry, dict):
            logger.warning("load_mcp_json: skipping non-dict entry %r", name)
            continue
        try:
            config = _parse_server_entry(name, entry)
            result[name] = config
        except Exception as exc:
            logger.warning("load_mcp_json: skipping %r: %s", name, exc)

    logger.debug("load_mcp_json: loaded %d servers from %s", len(result), path)
    return result


def _parse_server_entry(name: str, entry: dict[str, Any]) -> ServerConfig:
    """Parse a single server entry from .mcp.json into a ServerConfig."""
    server_type = entry.get("type", "stdio")

    if server_type == "stdio":
        return StdioServerConfig(
            command=entry["command"],
            args=entry.get("args", []),
            env=entry.get("env", {}),
        )
    elif server_type == "sse":
        return SSEServerConfig(
            type="sse",
            url=entry["url"],
            headers=entry.get("headers", {}),
        )
    elif server_type == "http":
        return HTTPServerConfig(
            type="http",
            url=entry["url"],
            headers=entry.get("headers", {}),
        )
    elif server_type == "ws":
        return WebSocketServerConfig(
            type="ws",
            url=entry["url"],
            headers=entry.get("headers", {}),
        )
    else:
        raise ValueError(f"unsupported server type {server_type!r}")


# ── Config merging ──────────────────────────────────────────────────


def merge_configs(
    primary: dict[str, ServerConfig],
    secondary: dict[str, ServerConfig],
) -> dict[str, ServerConfig]:
    """Merge *secondary* into *primary*; primary wins on name conflict.

    Args:
        primary: Higher-priority configs (from ConfigManager).
        secondary: Lower-priority configs (from .mcp.json).

    Returns:
        Merged dict (new object, inputs not mutated).
    """
    merged = dict(primary)
    for name, config in secondary.items():
        if name not in merged:
            merged[name] = config
        else:
            logger.debug(
                "merge_configs: %r already in primary — skipping .mcp.json entry",
                name,
            )
    return merged


# ── Policy filtering ────────────────────────────────────────────────


def filter_by_policy(
    servers: dict[str, ServerConfig],
    policy: MCPPolicyConfig | None = None,
) -> tuple[dict[str, ServerConfig], dict[str, ServerConfig]]:
    """Split *servers* into (allowed, disabled) based on *policy*.

    Deny rules take absolute precedence over allow rules, matching
    CC's ``filterMcpServersByPolicy()``.

    Args:
        servers: All candidate servers.
        policy: Policy config.  ``None`` = allow all.

    Returns:
        ``(allowed, disabled)`` — two disjoint dicts covering all input.
    """
    if policy is None:
        return dict(servers), {}

    denied = set(policy.denied_servers)
    allowed_set = set(policy.allowed_servers) if policy.allowed_servers is not None else None

    result_allowed: dict[str, ServerConfig] = {}
    result_disabled: dict[str, ServerConfig] = {}

    for name, config in servers.items():
        if name in denied:
            result_disabled[name] = config
        elif allowed_set is not None and name not in allowed_set:
            result_disabled[name] = config
        else:
            result_allowed[name] = config

    if result_disabled:
        logger.info(
            "filter_by_policy: disabled %d servers: %s",
            len(result_disabled),
            list(result_disabled.keys()),
        )

    return result_allowed, result_disabled
