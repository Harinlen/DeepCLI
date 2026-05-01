"""JSON-RPC 2.0 response dispatching for the MCP client.

Isolates the request/response correlation logic — parsing an
incoming message body, looking up the matching pending Future,
and resolving it with either the ``result`` or an
:class:`McpError`.  Kept separate from the client's transport
and reconnection concerns so the dispatch rules are stated in
one place and are easy to unit-test.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from daemon.errors import McpError

logger = logging.getLogger(__name__)


def dispatch_response(
    body: bytes,
    pending: dict[int, asyncio.Future[Any]],
    server_name: str,
) -> None:
    """Parse a JSON-RPC frame and resolve the matching pending Future.

    Notifications (messages without ``id``) are logged and dropped.
    Responses (messages with ``id``) match against ``pending`` — if
    the Future is already done (cancelled / timed out) the result
    is discarded silently.

    Args:
        body: Raw JSON bytes from the server.
        pending: Request-id → Future map owned by the client.  The
            matching entry is popped in-place.
        server_name: Logical server name, used only for logging.
    """
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON from MCP server '%s'", server_name)
        return

    # Notifications (no id) — log and ignore.
    if "id" not in msg:
        logger.debug(
            "MCP notification from '%s': %s",
            server_name,
            msg.get("method", "unknown"),
        )
        return

    request_id = msg["id"]
    future = pending.pop(request_id, None)
    if future is None or future.done():
        return

    if "error" in msg:
        err = msg["error"]
        code = err.get("code", -1)
        message = err.get("message", "Unknown MCP error")
        future.set_exception(McpError(f"[{code}] {message}"))
    else:
        future.set_result(msg.get("result", {}))


def reject_all_pending(pending: dict[int, asyncio.Future[Any]], reason: str) -> None:
    """Fail every pending request with ``McpError(reason)`` and clear the map."""
    for fut in pending.values():
        if not fut.done():
            fut.set_exception(McpError(reason))
    pending.clear()


__all__ = ["dispatch_response", "reject_all_pending"]
