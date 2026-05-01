"""Permission request/response bridge for a single WebSocket session.

When the orchestrator needs user approval for a tool call it yields
a :class:`PermissionRequest`.  The WS handler passes it to the CLI,
which replies with a ``permission_response`` message.  This class
bridges the two sides with an ``asyncio.Future`` per pending
request:

- :meth:`create_waiter` is called when a ``PermissionRequest`` goes
  out; it returns a Future the orchestrator-task awaits.
- :meth:`resolve` is called from the WS receive loop when the
  matching ``permission_response`` arrives.
- :meth:`cancel_all` is called on disconnect to fail any still-
  pending requests with ``deny``.

Kept in its own module so the concurrency contract around
permission round-trips is stated in one focused file.
"""

from __future__ import annotations

import asyncio

from daemon.engine.stream import PermissionResponse


class PermissionHandler:
    """Manages permission request/response round-trips over WebSocket.

    When the orchestrator needs user approval for a tool call, it
    yields a ``PermissionRequest``.  The WS handler sends this to
    the client and waits for the ``permission_response`` message.
    This class bridges the two via ``asyncio.Future`` objects.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[PermissionResponse]] = {}

    def create_waiter(self, request_id: str) -> asyncio.Future[PermissionResponse]:
        """Create a future that resolves when the client responds.

        Args:
            request_id: Unique ID matching the ``PermissionRequest``.

        Returns:
            Future resolving to a :class:`PermissionResponse` carrying
            the three-way decision (``allow`` / ``deny`` /
            ``always_allow``).
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionResponse] = loop.create_future()
        self._pending[request_id] = future
        return future

    def resolve(self, request_id: str, response: PermissionResponse) -> bool:
        """Resolve a pending permission request.

        Args:
            request_id: The request ID from the client's response.
            response: Parsed response carrying the user's decision.

        Returns:
            True if a pending request was found and resolved.
        """
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(response)
            return True
        return False

    @property
    def has_pending(self) -> bool:
        """Whether any permission requests are awaiting a response."""
        return any(not f.done() for f in self._pending.values())

    def cancel_all(self) -> None:
        """Cancel all pending requests (e.g. on disconnect)."""
        for request_id, future in self._pending.items():
            if not future.done():
                future.set_result(PermissionResponse(request_id=request_id, decision="deny"))
        self._pending.clear()


__all__ = ["PermissionHandler"]
