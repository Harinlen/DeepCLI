"""JSON-RPC 2.0 dispatch — correlate responses to pending requests.

Pure functions with no external dependencies.  ``McpClient`` uses
``dispatch_response()`` in its read loop and ``reject_all_pending()``
during close / reconnect to fail-fast any in-flight callers.
"""

from __future__ import annotations

import orjson
import logging
from asyncio import Future
from typing import Any

from kernel.mcp.types import McpError

logger = logging.getLogger(__name__)


def dispatch_response(
    raw: bytes,
    pending: dict[int, Future[Any]],
    server_name: str,
) -> None:
    """Parse *raw* as JSON-RPC and resolve/reject the matching future.

    Notifications (messages without an ``id``) are logged and dropped —
    the current protocol surface does not use them client-side.

    Args:
        raw: UTF-8 bytes of one JSON-RPC message.
        pending: ``{request_id: future}`` map owned by ``McpClient``.
        server_name: For log context.
    """
    try:
        body = orjson.loads(raw)
    except (orjson.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning(
            "jsonrpc[%s]: malformed message: %s",
            server_name,
            exc,
        )
        return

    # Notifications have no ``id``.
    msg_id = body.get("id")
    if msg_id is None:
        method = body.get("method", "<unknown>")
        logger.debug(
            "jsonrpc[%s]: notification %s (dropped)",
            server_name,
            method,
        )
        return

    future = pending.pop(msg_id, None)
    if future is None:
        # Stale / duplicate response — the caller already timed out
        # or cancelled.
        logger.debug(
            "jsonrpc[%s]: no pending future for id=%s (stale)",
            server_name,
            msg_id,
        )
        return

    if future.done():
        # Race with timeout/cancel — silently discard.
        return

    error = body.get("error")
    if error is not None:
        code = error.get("code")
        message = error.get("message", "unknown error")
        future.set_exception(McpError(message, code=code))
        return

    future.set_result(body.get("result"))


def reject_all_pending(
    pending: dict[int, Future[Any]],
    reason: str,
) -> None:
    """Fail every in-flight request with *reason*.

    Called during close or before a reconnect attempt so callers
    don't hang forever.  Clears the map after draining.

    Args:
        pending: The ``{request_id: future}`` map to drain.
        reason: Human-readable message for the ``McpError``.
    """
    for future in pending.values():
        if not future.done():
            future.set_exception(McpError(reason))
    pending.clear()
