"""MCP subsystem — MCP server connection lifecycle.

``MCPManager`` is the kernel Subsystem that owns all MCP server
connections.  It loads config, establishes connections (concurrently),
monitors health, and exposes a ``Signal`` that ``ToolManager``
subscribes to for tool-pool updates.

Mirrors Claude Code's ``services/mcp/client.ts`` orchestration layer
(``getMcpToolsCommandsAndResources``, ``reconnectMcpServerImpl``),
adapted to the Mustang Subsystem lifecycle.

Design doc: ``docs/plans/landed/mcp-manager.md``
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

from kernel.mcp.client import McpClient
from kernel.mcp.config import (
    MCPConfig,
    MCPPolicyConfig,
    ServerConfig,
    filter_by_policy,
    load_mcp_json,
    merge_configs,
)
from kernel.mcp.transport import create_transport
from kernel.mcp.types import (
    ConnectedServer,
    DisabledServer,
    FailedServer,
    McpAuthError,
    MCPServerConnection,
    McpError,
    McpResourceDef,
    McpResourceResult,
    McpToolDef,
    McpToolResult,
    NeedsAuthServer,
    TransportClosed,
)
from kernel.signal import Signal
from kernel.subsystem import Subsystem

logger = logging.getLogger(__name__)

# Concurrency limits for server connections (matches CC defaults).
_STDIO_CONCURRENCY: int = 3
_REMOTE_CONCURRENCY: int = 20

# Max consecutive failed-connect warnings per server before suppression.
# After this, further failures are silent until the server reconnects.
_FAIL_LOG_LIMIT: int = 3


class MCPManager(Subsystem):
    """Manages MCP server connections (stdio / SSE / HTTP / WebSocket).

    Handles connection lifecycle, health monitoring, and reconnection
    on disconnect.  Exposes live connections to ToolManager, which is
    responsible for discovering and registering proxy tools.

    Public API consumed by ToolManager and MCPAdapter:

    - ``on_tools_changed`` — Signal emitted when the connection set
      changes (startup, reconnect, config hot-reload).
    - ``get_connected()`` — snapshot of all active connections.
    - ``list_tools(server_name)`` — fetch tool defs from a server.
    - ``call_tool(server, tool, args)`` — execute an MCP tool.
    - ``reconnect(server_name)`` — manual reconnect trigger.
    """

    def __init__(self, module_table: Any) -> None:
        super().__init__(module_table)
        self._connections: dict[str, MCPServerConnection] = {}
        self._configs: dict[str, ServerConfig] = {}
        self._fail_counts: dict[str, int] = {}
        self._on_tools_changed: Signal[[]] = Signal()
        self._health_task: asyncio.Task[None] | None = None
        self._disconnect_config: Any = None  # Signal disconnect callable

    # ── Subsystem lifecycle ─────────────────────────────────────────

    async def startup(self) -> None:
        """Load config, connect servers, start health monitor.

        Steps:
        1. Bind ConfigManager section for MCP servers.
        2. Load ``.mcp.json`` and merge.
        3. Apply policy filtering (allowed/denied).
        4. Connect all enabled servers concurrently.
        5. Emit ``on_tools_changed`` so ToolManager picks up tools.
        6. Start the health-check background task.
        7. Subscribe to config changes for hot-reload.
        """
        # 1. Bind config section.
        try:
            section = self._module_table.config.bind_section(
                file="mcp",
                section="mcp",
                schema=MCPConfig,
            )
            cfg = section.get()
        except Exception:
            logger.exception("MCPManager: config bind failed — running with no MCP servers")
            cfg = MCPConfig()
            section = None

        # 2. Load .mcp.json from cwd (Claude Code project convention).
        mcp_json_servers = load_mcp_json(Path.cwd() / ".mcp.json")
        all_servers = merge_configs(cfg.servers, mcp_json_servers)

        # 3. Policy filtering.
        policy = self._load_policy()
        allowed, disabled = filter_by_policy(all_servers, policy)

        # Record disabled servers.
        for name in disabled:
            self._connections[name] = DisabledServer(name=name)

        # Store configs for reconnect.
        self._configs = allowed

        # 4. Connect.
        if allowed:
            await self._connect_batch(allowed)
            connected = sum(1 for c in self._connections.values() if isinstance(c, ConnectedServer))
            logger.info(
                "MCPManager: %d/%d servers connected",
                connected,
                len(allowed),
            )
        else:
            logger.info("MCPManager: no MCP servers configured")

        # 5. Emit signal.
        await self._on_tools_changed.emit()

        # 6. Health monitor.
        from kernel.mcp.health import health_loop

        self._health_task = asyncio.create_task(health_loop(self), name="mcp-health")

        # 7. Config hot-reload.
        if section is not None:
            self._disconnect_config = section.changed.connect(self._on_config_changed)

    async def shutdown(self) -> None:
        """Cancel health task, close all connections, clean up."""
        # Cancel health monitor.
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._health_task

        # Close all connected clients.
        close_tasks = [
            conn.client.close()
            for conn in self._connections.values()
            if isinstance(conn, ConnectedServer)
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        # Disconnect config signal.
        if self._disconnect_config is not None:
            self._disconnect_config()

        self._connections.clear()
        self._configs.clear()
        self._fail_counts.clear()

    # ── Public API ──────────────────────────────────────────────────

    @property
    def on_tools_changed(self) -> Signal[[]]:
        """Signal emitted when the set of connected servers changes.

        ToolManager connects to this during its own ``startup()``
        via ``on_tools_changed.connect(self._sync_mcp)``.
        """
        return self._on_tools_changed

    def get_connections(self) -> dict[str, MCPServerConnection]:
        """Snapshot of all connection states."""
        return dict(self._connections)

    def get_connected(self) -> list[ConnectedServer]:
        """Convenience: only connected servers."""
        return [c for c in self._connections.values() if isinstance(c, ConnectedServer)]

    async def list_tools(self, server_name: str) -> list[McpToolDef]:
        """Fetch tool definitions from a connected server.

        Args:
            server_name: Server identifier.

        Returns:
            Tool definitions.  Empty list if server not connected.
        """
        conn = self._connections.get(server_name)
        if not isinstance(conn, ConnectedServer):
            logger.warning("list_tools: %r is not connected", server_name)
            return []
        try:
            return await conn.client.list_tools()
        except (McpError, TransportClosed) as exc:
            logger.warning("list_tools[%s]: %s", server_name, exc)
            return []

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpToolResult:
        """Execute an MCP tool on a connected server.

        Called by ``MCPAdapter.call()`` during tool execution.
        On 401, attempts one token refresh + reconnect before failing.

        Args:
            server_name: Server identifier.
            tool_name: Tool name (server-local, not prefixed).
            arguments: Tool input dict.

        Raises:
            McpError: If the server is not connected or the call fails.
        """
        conn = self._connections.get(server_name)
        if not isinstance(conn, ConnectedServer):
            raise McpError(f"server {server_name!r} is not connected")
        try:
            return await conn.client.call_tool(tool_name, arguments)
        except McpAuthError:
            # Attempt one token refresh + reconnect.
            if self._module_table.secrets:
                from kernel.mcp.oauth import try_refresh_token
                refreshed = await try_refresh_token(server_name, self._module_table.secrets)
                if refreshed:
                    new_conn = await self.reconnect_with_token(
                        server_name, refreshed.access_token
                    )
                    if isinstance(new_conn, ConnectedServer):
                        return await new_conn.client.call_tool(tool_name, arguments)
            # Refresh failed or no secrets — transition to NeedsAuth.
            server_url = self.get_server_url(server_name) or ""
            self._connections[server_name] = NeedsAuthServer(
                name=server_name, server_url=server_url
            )
            await self._on_tools_changed.emit()
            raise

    async def list_resources(self, server_name: str) -> list[McpResourceDef]:
        """Fetch resource definitions from a connected server.

        Args:
            server_name: Server identifier.

        Returns:
            Resource definitions.  Empty list if server not connected or
            does not advertise the ``resources`` capability.
        """
        conn = self._connections.get(server_name)
        if not isinstance(conn, ConnectedServer):
            logger.warning("list_resources: %r is not connected", server_name)
            return []
        if "resources" not in conn.capabilities:
            return []
        try:
            return await conn.client.list_resources()
        except (McpError, TransportClosed) as exc:
            logger.warning("list_resources[%s]: %s", server_name, exc)
            return []

    async def read_resource(self, server_name: str, uri: str) -> McpResourceResult:
        """Read a specific resource from a connected server.

        Args:
            server_name: Server identifier.
            uri: Resource URI.

        Returns:
            Resource result with contents list.

        Raises:
            McpError: If the server is not connected, does not support
                resources, or the read fails.
        """
        conn = self._connections.get(server_name)
        if not isinstance(conn, ConnectedServer):
            raise McpError(f"server {server_name!r} is not connected")
        if "resources" not in conn.capabilities:
            raise McpError(f"server {server_name!r} does not support resources")
        return await conn.client.read_resource(uri)

    def get_server_url(self, server_name: str) -> str | None:
        """Return the URL for a remote server, or ``None`` for stdio."""
        config = self._configs.get(server_name)
        if config is None:
            return None
        return getattr(config, "url", None)

    async def reconnect(self, server_name: str) -> MCPServerConnection:
        """Reconnect a specific server.

        Closes any existing connection, creates a fresh one.
        Checks SecretManager for OAuth tokens automatically.
        Updates ``self._connections`` in place.

        Args:
            server_name: Server to reconnect.

        Returns:
            The new connection state.
        """
        config = self._configs.get(server_name)
        if config is None:
            logger.warning("reconnect: no config for %r", server_name)
            return FailedServer(name=server_name, error="no config")

        # Close existing connection if any.
        old = self._connections.get(server_name)
        if isinstance(old, ConnectedServer):
            await old.client.close()

        # _connect_one auto-fetches OAuth tokens from SecretManager.
        new_conn = await self._connect_one(server_name, config)
        self._connections[server_name] = new_conn
        return new_conn

    async def reconnect_with_token(
        self,
        server_name: str,
        access_token: str,
    ) -> MCPServerConnection:
        """Reconnect a server with an explicit Bearer token.

        Used by McpAuthTool after a successful OAuth flow.

        Args:
            server_name: Server to reconnect.
            access_token: The OAuth access token to inject.

        Returns:
            The new connection state.
        """
        config = self._configs.get(server_name)
        if config is None:
            logger.warning("reconnect_with_token: no config for %r", server_name)
            return FailedServer(name=server_name, error="no config")

        old = self._connections.get(server_name)
        if isinstance(old, ConnectedServer):
            await old.client.close()

        auth_headers = {"Authorization": f"Bearer {access_token}"}
        new_conn = await self._connect_one(server_name, config, auth_headers=auth_headers)
        self._connections[server_name] = new_conn
        return new_conn

    # ── Internal: connection management ─────────────────────────────

    async def _connect_batch(
        self,
        servers: dict[str, ServerConfig],
    ) -> None:
        """Connect servers concurrently, split by transport type.

        Matches CC's ``getMcpToolsCommandsAndResources``:
        - stdio (local): concurrency = 3
        - remote (sse/http/ws): concurrency = 20
        """
        local: dict[str, ServerConfig] = {
            n: c for n, c in servers.items() if c.type == "stdio"
        }
        remote: dict[str, ServerConfig] = {
            n: c for n, c in servers.items() if c.type != "stdio"
        }

        await asyncio.gather(
            self._connect_with_limit(local, _STDIO_CONCURRENCY),
            self._connect_with_limit(remote, _REMOTE_CONCURRENCY),
        )

    async def _connect_with_limit(
        self,
        servers: dict[str, ServerConfig],
        max_concurrency: int,
    ) -> None:
        """Connect *servers* with a semaphore-bounded concurrency."""
        if not servers:
            return

        sem = asyncio.Semaphore(max_concurrency)

        async def _guarded(name: str, config: ServerConfig) -> None:
            async with sem:
                conn = await self._connect_one(name, config)
                self._connections[name] = conn

        await asyncio.gather(
            *(_guarded(n, c) for n, c in servers.items()),
            return_exceptions=True,
        )

    async def _connect_one(
        self,
        name: str,
        config: ServerConfig,
        *,
        auth_headers: dict[str, str] | None = None,
    ) -> MCPServerConnection:
        """Attempt to connect a single server.

        Never raises — returns ``ConnectedServer``, ``NeedsAuthServer``,
        or ``FailedServer``.

        If *auth_headers* is provided, they are merged into the
        transport's headers (for OAuth Bearer tokens).
        """
        try:
            # Check SecretManager for an existing OAuth token.
            if auth_headers is None and self._module_table.secrets:
                token = self._module_table.secrets.get_oauth_token(name)
                if token:
                    # Proactive refresh if near expiry (< 120s).
                    if token.expires_at:
                        from datetime import datetime, timezone
                        remaining = (token.expires_at - datetime.now(timezone.utc)).total_seconds()
                        if remaining < 120 and token.refresh_token:
                            from kernel.mcp.oauth import try_refresh_token
                            refreshed = await try_refresh_token(name, self._module_table.secrets)
                            if refreshed:
                                token = refreshed
                            # If refresh failed, try with existing token anyway.
                    auth_headers = {"Authorization": f"Bearer {token.access_token}"}

            transport = create_transport(name, config, auth_headers=auth_headers)
            client = McpClient(transport, server_name=name)

            # Wire reconnect callback so health monitor gets notified.
            async def _on_reconnect() -> None:
                await self._on_tools_changed.emit()

            client.on_reconnect = _on_reconnect

            # Wire auth-required callback for runtime 401s.
            async def _on_auth_required() -> None:
                server_url = self.get_server_url(name) or ""
                self._connections[name] = NeedsAuthServer(name=name, server_url=server_url)
                await self._on_tools_changed.emit()

            client.on_auth_required = _on_auth_required

            capabilities = await client.connect()

            # Recovery notice: server is up after prior failures.
            prior_fails = self._fail_counts.pop(name, 0)
            if prior_fails:
                logger.info(
                    "MCPManager: %r reconnected after %d failed attempt%s",
                    name,
                    prior_fails,
                    "" if prior_fails == 1 else "s",
                )

            return ConnectedServer(
                name=name,
                client=client,
                capabilities=capabilities,
                server_info=client.server_info,
                instructions=client.instructions,
            )
        except McpAuthError:
            logger.info("MCPManager: server %r requires OAuth authentication", name)
            server_url = self.get_server_url(name) or ""
            return NeedsAuthServer(name=name, server_url=server_url)
        except (McpError, TransportClosed, OSError) as exc:
            self._log_connect_failure(name, exc)
            return FailedServer(name=name, error=str(exc))
        except Exception as exc:
            # Unexpected errors (bugs) always log with traceback — not rate-limited.
            logger.exception("MCPManager: unexpected error connecting %r", name)
            return FailedServer(name=name, error=str(exc))

    def _log_connect_failure(self, name: str, exc: BaseException) -> None:
        """Rate-limited warning for repeated connect failures.

        Emits a warning for each of the first ``_FAIL_LOG_LIMIT``
        consecutive failures.  On the boundary failure, appends a
        "suppressing further warnings" notice so the silence that
        follows is attributable.  Counter is reset on successful
        reconnect (see ``_connect_one``).
        """
        count = self._fail_counts.get(name, 0) + 1
        self._fail_counts[name] = count
        if count < _FAIL_LOG_LIMIT:
            logger.warning("MCPManager: failed to connect %r: %s", name, exc)
        elif count == _FAIL_LOG_LIMIT:
            logger.warning(
                "MCPManager: failed to connect %r: %s "
                "(suppressing further warnings until reconnect)",
                name,
                exc,
            )

    # ── Internal: config hot-reload ─────────────────────────────────

    async def _on_config_changed(
        self,
        old: MCPConfig,
        new: MCPConfig,
    ) -> None:
        """Respond to ConfigManager section updates.

        Adds new servers, removes dropped servers, emits signal.
        """
        old_names = set(old.servers)
        new_names = set(new.servers)

        # Disconnect removed servers.
        for name in old_names - new_names:
            conn = self._connections.pop(name, None)
            self._configs.pop(name, None)
            self._fail_counts.pop(name, None)
            if isinstance(conn, ConnectedServer):
                await conn.client.close()
                logger.info("MCPManager: removed server %r", name)

        # Connect new servers.
        added: dict[str, ServerConfig] = {}
        for name in new_names - old_names:
            config = new.servers[name]
            self._configs[name] = config
            added[name] = config

        if added:
            await self._connect_batch(added)

        if old_names != new_names:
            await self._on_tools_changed.emit()

    # ── Internal: policy ────────────────────────────────────────────

    def _load_policy(self) -> MCPPolicyConfig | None:
        """Try to read MCP policy from ConfigManager."""
        try:
            section = self._module_table.config.get_section(
                file="config",
                section="mcp_policy",
                schema=MCPPolicyConfig,
            )
            return section.get()
        except Exception:
            # Policy section doesn't exist or is invalid — allow all.
            return None
