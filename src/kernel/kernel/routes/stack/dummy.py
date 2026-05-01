"""Identity stack — the placeholder until ACP codec / dispatcher land.

``DummyCodec`` and ``DummyDispatcher`` are paired by ``M = str``:
the codec passes raw strings through unchanged and the dispatcher
yields each inbound string right back.  Plugged into the transport
loop, the visible behavior is a pure echo, but it goes through the
same :class:`kernel.routes.stack.ProtocolStack` interface the real
ACP stack will use — so transport-level tests exercise the same
code path regardless of which stack is live.

Why "dummy" and not "echo"
--------------------------
The name is deliberate: this is not "a special echo feature" sitting
alongside the real stack, it is a *degenerate implementation* of the
same abstraction that exists purely so the three-layer architecture
can be wired up and tested before protocol / session layers are
written.  Users who run it see echo behavior as a side effect, not
as a feature.

When the real ACP stack lands the dummy stack will not be removed
immediately — it remains a useful smoke-test handle for transport
work that doesn't care about protocol semantics.  Switching the
default will be a one-line change in
:class:`kernel.routes.flags.TransportFlags`.
"""

from __future__ import annotations

import orjson
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from kernel.routes.stack import ProtocolError

if TYPE_CHECKING:
    from kernel.connection_auth import AuthContext


class DummyCodec:
    """Identity ``ProtocolCodec[str]`` — raw strings pass through.

    ``decode`` cannot actually fail in this codec (any string is a
    valid "message"), so :class:`ProtocolError` is never raised and
    :meth:`encode_error` is effectively unreachable.  The method is
    still implemented so the class conforms structurally to the
    :class:`kernel.routes.stack.ProtocolCodec` Protocol.
    """

    def decode(self, raw: str) -> str:
        return raw

    def encode(self, msg: str) -> str:
        return msg

    def encode_error(self, error: ProtocolError) -> str:
        # Shape chosen so clients can pattern-match ``"error"`` key
        # without parsing surprises — we are not bound to any
        # particular schema here because there is no protocol spec
        # yet, but staying close to JSON makes the transcript look
        # sane in Postman / websocat while we debug.
        return orjson.dumps({"error": str(error)}).decode()


class DummyDispatcher:
    """Identity ``SessionDispatcher[str]`` — yields input unchanged.

    Async generator so it conforms to the
    :class:`kernel.routes.stack.SessionDispatcher` Protocol (which
    demands :class:`AsyncIterator`).  One inbound message produces
    exactly one outbound message, which when combined with
    :class:`DummyCodec` gives the transport an end-to-end echo.
    """

    async def dispatch(self, msg: str, auth: AuthContext) -> AsyncIterator[str]:
        yield msg

    async def on_disconnect(self, auth: AuthContext) -> None:
        pass  # echo stack has no in-flight state to clean up
