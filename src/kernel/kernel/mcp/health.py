"""Health monitor — periodic reconnection of failed MCP servers.

Runs as a background ``asyncio.Task`` inside ``MCPManager``.  Every
``interval`` seconds it scans connections for ``FailedServer`` entries,
attempts to reconnect them, and emits ``on_tools_changed`` if any
succeed — so ToolManager can refresh proxy tools.

Mirrors CC's error-tracking + reconnect pattern in ``client.ts``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from kernel.mcp.types import ConnectedServer, FailedServer

if TYPE_CHECKING:
    from kernel.mcp import MCPManager

logger = logging.getLogger(__name__)

# Default interval between health-check sweeps (seconds).
_DEFAULT_INTERVAL: float = 60.0


async def health_loop(
    manager: MCPManager,
    *,
    interval: float = _DEFAULT_INTERVAL,
) -> None:
    """Periodically scan for failed servers and attempt reconnection.

    This coroutine is designed to run as a long-lived background task.
    It exits cleanly on ``CancelledError`` (manager shutdown).

    Args:
        manager: The owning MCPManager instance.
        interval: Seconds between sweeps.
    """
    try:
        while True:
            await asyncio.sleep(interval)
            changed = await _sweep(manager)
            if changed:
                await manager.on_tools_changed.emit()
    except asyncio.CancelledError:
        logger.debug("health_loop: cancelled — exiting")


async def _sweep(manager: MCPManager) -> bool:
    """One pass: try to reconnect every failed server.

    Returns:
        ``True`` if at least one server transitioned to connected.
    """
    changed = False
    for name, conn in list(manager.get_connections().items()):
        if not isinstance(conn, FailedServer):
            continue

        logger.debug("health_loop: attempting reconnect for %r", name)
        new_conn = await manager.reconnect(name)
        if isinstance(new_conn, ConnectedServer):
            logger.info("health_loop: reconnected %r", name)
            changed = True

    return changed
