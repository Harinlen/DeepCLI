"""Background health monitor for MCP server connections.

The MCP client detects disconnection via its own read-loop
(:class:`TransportClosed` exception) and schedules a reconnect.
But if a transport dies while idle (no reads in flight) the client
won't notice until it next tries to send a request.  This module
runs a periodic poll that checks each client's transport status
and kicks off the reconnect path when the connection has dropped.

Uses only public APIs on :class:`McpClient` — no private-attribute
access.  The client's :meth:`handle_transport_closed` method
encapsulates all state cleanup and reconnection logic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from daemon.extensions.mcp.client import McpClient
from daemon.lifecycle import register_cleanup

logger = logging.getLogger(__name__)

# How often to poll for dead MCP connections (seconds).
HEALTH_CHECK_INTERVAL = 30


def start_health_monitor(clients: list[McpClient]) -> asyncio.Task[None]:
    """Launch the background health-check task for *clients*.

    The returned task is registered with the lifecycle module for
    cancellation at shutdown; callers typically just hold a
    reference for diagnostics.

    Args:
        clients: The mutable list of MCP clients to watch.  The
            monitor reads from the list on each poll, so newly
            registered clients are picked up automatically.

    Returns:
        The spawned monitor task.
    """
    task = asyncio.create_task(_monitor_loop(clients), name="mcp-health-monitor")

    async def _cancel() -> None:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    register_cleanup(_cancel)
    return task


async def _monitor_loop(clients: Iterable[McpClient]) -> None:
    """Periodically check each client's transport health.

    If a client reports as connected but its transport says otherwise,
    trigger the client's own reconnect path via the public
    :meth:`handle_transport_closed` method.
    """
    try:
        while True:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            for client in clients:
                # Skip clients that are already disconnected or closing
                if not client.is_connected:
                    continue

                # Check the transport's own health signal
                if not client.transport.is_connected:
                    logger.warning(
                        "MCP server '%s' transport down — triggering reconnect via health monitor",
                        client.server_name,
                    )
                    await client.handle_transport_closed("Health monitor: transport not connected")
    except asyncio.CancelledError:
        pass


__all__ = ["HEALTH_CHECK_INTERVAL", "start_health_monitor"]
