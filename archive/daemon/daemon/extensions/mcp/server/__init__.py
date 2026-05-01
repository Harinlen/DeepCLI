"""In-process MCP server framework.

Provides :class:`McpServerProtocol`, the ABC for MCP servers that
run inside the daemon process (via :class:`InProcessTransport`).
"""

from daemon.extensions.mcp.server.protocol import McpServerProtocol

__all__ = ["McpServerProtocol"]
