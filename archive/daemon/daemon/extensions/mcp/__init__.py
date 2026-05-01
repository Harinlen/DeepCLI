"""MCP integration — connect to external MCP servers.

Provides client, bridge, config loading, and result storage for the
Model Context Protocol.  MCP server tools are exposed as regular
Mustang tools in the shared ``ToolRegistry``.

Transport layer is pluggable: stdio (Phase 3), in-process, HTTP/SSE,
and WebSocket transports are supported via the ``transport`` package.
"""

from daemon.extensions.mcp.bridge import McpBridge
from daemon.extensions.mcp.client import McpClient
from daemon.extensions.mcp.config import McpServerEntry, load_mcp_config
from daemon.extensions.mcp.result_store import McpResultStore
from daemon.extensions.mcp.transport import Transport, create_transport

__all__ = [
    "McpBridge",
    "McpClient",
    "McpResultStore",
    "McpServerEntry",
    "Transport",
    "create_transport",
    "load_mcp_config",
]
