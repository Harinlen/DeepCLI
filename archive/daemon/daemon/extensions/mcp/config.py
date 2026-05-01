"""MCP server configuration — merge ``mcp.json`` and ``config.yaml``.

Two sources supply MCP server definitions:

1. ``~/.mustang/mcp.json`` — standalone JSON file (Claude Code style).
2. ``config.yaml`` ``mcp_servers`` section — inline in the main config.

Both are optional.  When the same server name appears in both,
``mcp.json`` wins (more specific file takes precedence).

Phase 3 only supports ``stdio`` transport.  Servers with other types
are logged as warnings and skipped.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daemon.config.schema import McpServerRuntimeConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class McpServerEntry:
    """A single MCP server's resolved configuration.

    Attributes:
        name: Logical name (used in tool name prefix).
        type: Transport type — ``"stdio"``, ``"inprocess"``,
            ``"sse"``, or ``"ws"``.
        command: Executable to spawn (stdio only).
        args: Command-line arguments (stdio only).
        env: Extra environment variables for the subprocess.
        module: Python module path for in-process servers.
        class_name: Class name within *module* for in-process servers.
        url: Remote endpoint URL (SSE / WebSocket only).
        headers: HTTP headers for remote transports.
        tools_concurrency: Per-tool concurrency overrides.  Keys are
            the **original** MCP tool names (not the ``mcp__``
            prefixed form).  Values are ``"parallel"`` or ``"keyed"``.
            Tools not listed default to ``"serial"``.
    """

    name: str
    type: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    module: str = ""
    class_name: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    tools_concurrency: dict[str, str] = field(default_factory=dict)


def load_mcp_config(
    mcp_json_path: Path,
    config_servers: dict[str, McpServerRuntimeConfig],
) -> list[McpServerEntry]:
    """Merge MCP server definitions from two sources.

    Args:
        mcp_json_path: Path to ``~/.mustang/mcp.json``.
        config_servers: ``mcp_servers`` section from the resolved
            runtime config (``config.yaml``).

    Returns:
        List of validated ``McpServerEntry`` objects, ready to connect.
    """
    json_servers = _load_json_file(mcp_json_path)
    merged = _merge_sources(json_servers, config_servers)
    return _validate_entries(merged)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _load_json_file(path: Path) -> dict[str, Any]:
    """Read and parse ``mcp.json``.

    Returns an empty dict on missing file or parse errors.
    """
    if not path.is_file():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse %s: %s — skipping", path, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("mcp.json root must be an object — skipping")
        return {}

    # Accept both top-level keys and nested "servers" key
    servers = raw.get("servers", raw)
    if not isinstance(servers, dict):
        logger.warning("mcp.json 'servers' must be an object — skipping")
        return {}

    return servers


def _merge_sources(
    json_servers: dict[str, Any],
    config_servers: dict[str, McpServerRuntimeConfig],
) -> dict[str, dict[str, Any]]:
    """Merge two sources — ``mcp.json`` overrides ``config.yaml``.

    Returns a name → raw-dict mapping.
    """
    merged: dict[str, dict[str, Any]] = {}

    # config.yaml entries first (lower priority)
    for name, cfg in config_servers.items():
        merged[name] = cfg.model_dump()

    # mcp.json entries override
    for name, entry in json_servers.items():
        if not isinstance(entry, dict):
            logger.warning("mcp.json entry '%s' is not an object — skipping", name)
            continue
        merged[name] = entry

    return merged


_SUPPORTED_TYPES = {"stdio", "inprocess", "sse", "ws"}


def _validate_entries(merged: dict[str, dict[str, Any]]) -> list[McpServerEntry]:
    """Validate merged entries and build typed config objects.

    Entries with unsupported types or missing required fields for
    their type are warned and skipped.
    """
    entries: list[McpServerEntry] = []

    for name, raw in merged.items():
        transport = raw.get("type", "stdio")

        if transport not in _SUPPORTED_TYPES:
            logger.warning(
                "MCP server '%s' uses unsupported transport '%s' — skipping",
                name,
                transport,
            )
            continue

        # Type-specific required-field checks
        if transport == "stdio":
            command = raw.get("command")
            if not command:
                logger.warning(
                    "MCP server '%s' (stdio) has no 'command' — skipping",
                    name,
                )
                continue
        elif transport == "inprocess":
            if not raw.get("module") or not raw.get("class"):
                logger.warning(
                    "MCP server '%s' (inprocess) needs 'module' and 'class' — skipping",
                    name,
                )
                continue
        elif transport in ("sse", "ws"):
            if not raw.get("url"):
                logger.warning(
                    "MCP server '%s' (%s) has no 'url' — skipping",
                    name,
                    transport,
                )
                continue

        # tools_concurrency: {"tool_name": "parallel"|"keyed"}
        raw_tc = raw.get("tools_concurrency") or {}
        if not isinstance(raw_tc, dict):
            logger.warning(
                "MCP server '%s' tools_concurrency must be an object — ignoring",
                name,
            )
            raw_tc = {}

        entries.append(
            McpServerEntry(
                name=name,
                type=transport,
                command=raw.get("command") or "",
                args=raw.get("args") or [],
                env=raw.get("env") or {},
                module=raw.get("module") or "",
                class_name=raw.get("class") or "",
                url=raw.get("url") or "",
                headers=raw.get("headers") or {},
                tools_concurrency=raw_tc,
            )
        )

    return entries
