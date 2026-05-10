"""ClientSender — the protocol layer's outbound channel to the client.

Handlers receive a ``ClientSender`` via :class:`HandlerContext` and use
it to push notifications or issue outgoing requests without ever
touching a WebSocket handle.  The concrete implementation lives in the
ACP sub-package; this module only declares the structural ``Protocol``
that any implementation must satisfy.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class ClientSender(Protocol):
    """Capability injection: send messages from kernel → client.

    Each instance is scoped to a **single connection**.  The underlying
    WebSocket is captured in a closure inside the ACP implementation;
    handlers never see it.

    Thread / task safety
    --------------------
    Both methods are coroutines and must be awaited.  Concurrent calls
    from different tasks are safe — the implementation serialises writes
    to the underlying socket.
    """

    async def notify(
        self,
        method: str,
        params: BaseModel,
    ) -> None:
        """Send an outgoing JSON-RPC **notification** (no response expected).

        Parameters
        ----------
        method:
            One of the registered outgoing notification methods, e.g.
            ``"session/update"``.
        params:
            Pydantic model that will be serialised as the ``params``
            field of the notification frame.

        Raises
        ------
        ValueError
            If ``method`` is not a registered outgoing notification.
        """
        ...

    async def request(
        self,
        method: str,
        params: BaseModel,
        *,
        result_type: type[T],
        timeout: float | None = None,
    ) -> T:
        """Send an outgoing JSON-RPC **request** and await the response.

        Allocates a fresh request id, sends the frame, registers a
        ``Future`` in the in-flight map, and waits for the client's
        response.

        Parameters
        ----------
        method:
            One of the registered outgoing request methods, e.g.
            ``"session/request_permission"``.
        params:
            Pydantic model serialised as the ``params`` field.
        result_type:
            The expected Pydantic model class for the response
            ``result`` field.  Validated with ``model_validate``.
        timeout:
            Optional seconds before :exc:`TimeoutError` is raised.
            ``None`` means wait indefinitely.

        Raises
        ------
        TimeoutError
            Response did not arrive within ``timeout`` seconds.
        asyncio.CancelledError
            The calling task was cancelled.  The in-flight Future is
            removed from the map in the ``finally`` block so the
            pending slot does not leak.
        """
        ...

    def pending_request_ids(self) -> list[Any]:
        """Return the ids of all outgoing requests awaiting a response.

        Used by :meth:`on_disconnect` cleanup and by the cancel flow
        to enumerate what needs to be abandoned.
        """
        ...
