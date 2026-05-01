"""Protocol stack — the codec + dispatcher pair injected into transport.

See ``docs/subsystems/transport.md`` for the architectural rationale.
Short version: the WebSocket ``/session`` transport layer owns a
fixed ``recv → decode → dispatch → encode → send`` loop.  Everything
variable about "what this kernel does with incoming messages" lives
behind two ``typing.Protocol`` interfaces (:class:`ProtocolCodec`,
:class:`SessionDispatcher`) which are combined into a frozen
:class:`ProtocolStack` and selected at startup via
:class:`kernel.routes.flags.TransportFlags`.

Adding a new stack
------------------
1. Implement a codec class with ``decode`` / ``encode`` /
   ``encode_error`` and a dispatcher class with ``dispatch``.
2. Add the stack name to :data:`StackName` (a ``Literal`` type, so
   mypy forces you to handle it in step 3).
3. Add a branch in :func:`create_stack` that constructs the
   codec / dispatcher pair and wraps them in a :class:`ProtocolStack`.

The ``Literal`` + exhaustive ``if`` pattern is on purpose: adding a
new stack is a two-file change, but mypy's exhaustiveness check
catches the "I forgot to wire the factory" class of bug at static
analysis time instead of at the first inbound connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol, TypeVar

if TYPE_CHECKING:
    from kernel.connection_auth import AuthContext
    from kernel.module_table import KernelModuleTable


M = TypeVar("M")
"""Typed message shape flowing between codec and dispatcher.

A codec and dispatcher are **paired by** ``M``: the codec produces
``M`` instances from raw strings and the dispatcher consumes them.
For the dummy stack ``M`` is ``str`` (identity pass-through); for
a future ACP stack it would be a discriminated Pydantic union of
JSON-RPC method calls / responses / notifications.

The transport layer treats ``M`` as opaque — it does not unpack
messages, only shuffles them between codec and dispatcher.
"""


class ProtocolError(Exception):
    """Raised by :meth:`ProtocolCodec.decode` on malformed input.

    Transport catches this, asks the codec to format an error frame
    via :meth:`ProtocolCodec.encode_error`, sends that frame, and
    continues the recv loop — a single bad message does **not**
    break the connection, because the next message from the same
    client might still be well-formed.
    """


class ProtocolCodec(Protocol[M]):
    """JSON ↔ typed message codec with zero session awareness.

    Implementations are expected to be pure functions (no mutable
    state) so a single codec instance can serve the lifetime of a
    stack without per-connection allocation.
    """

    def decode(self, raw: str) -> M:
        """Parse an inbound frame into a typed message.

        Raises
        ------
        ProtocolError
            On any parse failure — missing field, wrong type,
            unknown method, etc.  Transport will reply with a
            :meth:`encode_error` frame and keep the socket open.
        """
        ...

    def encode(self, msg: M) -> str:
        """Serialize an outbound typed message.

        Must not fail — the message came from the dispatcher, which
        is trusted to produce codec-compatible values.  If it does
        fail, that's a codec or dispatcher implementation bug and
        the error propagates to transport as an unhandled
        exception (transport will close with 1011).
        """
        ...

    def encode_error(self, error: ProtocolError) -> str:
        """Format a protocol-level error as an outbound frame.

        Called by transport after :meth:`decode` raises.  The
        resulting string must be transmissible over the same wire
        format as a normal frame so the client can distinguish
        error frames from regular messages and surface them.
        """
        ...


class SessionDispatcher(Protocol[M]):
    """Inbound typed message → async stream of outbound typed messages.

    ``dispatch`` returns an async iterator because a single inbound
    message may produce zero or more outbound messages.  ACP's
    ``session/prompt`` is the canonical example: one inbound call,
    a long stream of ``session/update`` notifications, then a final
    response.

    Dispatchers may carry state (per-connection or per-process) but
    must **not** touch the WebSocket directly — they yield messages
    and transport does the writing.  This keeps the socket / socket
    error handling in exactly one place.
    """

    def dispatch(self, msg: M, auth: AuthContext) -> AsyncIterator[M]:
        """Handle one inbound message and yield responses."""
        ...

    async def on_disconnect(self, auth: AuthContext) -> None:
        """Called by transport when the WebSocket closes (cleanly or not).

        Implementations should use this to cancel any in-flight tasks
        spawned for this connection and unbind the connection from any
        active sessions.  The default is a no-op so simple / test
        dispatchers do not have to implement it.
        """
        ...


@dataclass(frozen=True)
class ProtocolStack(Generic[M]):
    """Frozen ``(codec, dispatcher)`` pair handed to transport.

    Codec and dispatcher **must** agree on ``M`` — violating that
    is a static type error, not a runtime surprise.  Transport uses
    ``ProtocolStack[Any]`` because transport itself is
    ``M``-agnostic; the type parameter exists purely to keep the
    two halves consistent inside any given stack.
    """

    codec: ProtocolCodec[M]
    dispatcher: SessionDispatcher[M]


StackName = Literal["dummy", "acp"]
"""Registered stack names.

Extend this tuple when adding a new stack and add a matching branch
in :func:`create_stack`.  Because it is a ``Literal``, pydantic
validates :class:`kernel.routes.flags.TransportFlags.stack` against
it at flag registration time — an unknown name in ``flags.yaml``
aborts kernel startup with a clear error, so transport never has to
guard against it at runtime.
"""


def create_stack(name: StackName, module_table: KernelModuleTable) -> ProtocolStack[Any]:
    """Factory: materialize the stack named ``name``.

    Parameters
    ----------
    name:
        One of the values enumerated by :data:`StackName`.
    module_table:
        Passed through so future stacks (notably the real ACP
        stack) can reach provider / session / memory subsystems.
        The dummy stack ignores it.

    Returns
    -------
    ProtocolStack[Any]
        The concrete ``M`` varies by branch (``str`` for the dummy
        stack, a Pydantic union for ACP), and ``ProtocolStack`` is
        invariant in ``M``, so there is no common parameter that
        subsumes all branches.  ``Any`` is the deliberate escape
        hatch at this single "pick one of several" seam —
        transport only reads ``codec`` / ``dispatcher`` and hands
        messages straight from one to the other without inspecting
        their concrete type, so no real type safety is lost.
    """
    if name == "dummy":
        from kernel.routes.stack.dummy import DummyCodec, DummyDispatcher

        return ProtocolStack(codec=DummyCodec(), dispatcher=DummyDispatcher())

    if name == "acp":
        from kernel.protocol import build_protocol_stack

        return build_protocol_stack(module_table)  # type: ignore[return-value]

    # ``name`` is a Literal, so mypy refuses any StackName that is
    # not handled above.  The runtime fallback exists only to give
    # a useful error if someone sneaks a raw string past the type
    # checker (e.g. dynamic config).
    raise AssertionError(f"unreachable: unknown stack name {name!r}")


__all__ = [
    "M",
    "ProtocolCodec",
    "ProtocolError",
    "ProtocolStack",
    "SessionDispatcher",
    "StackName",
    "create_stack",
]
