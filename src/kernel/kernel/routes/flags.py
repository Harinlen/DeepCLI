"""Flag schema for the WebSocket transport layer.

Transport is not a :class:`kernel.subsystem.Subsystem` — its
lifecycle is bound to the FastAPI server itself, not to the
kernel's subsystem loader — so it has no ``startup`` hook to
register a flag section from.  Instead the kernel lifespan calls
``FlagManager.register("transport", TransportFlags)`` directly
right after :meth:`kernel.flags.FlagManager.initialize`, mirroring
the way FlagManager pre-registers the built-in ``kernel`` section.

Why a flag and not a config
---------------------------
See ``docs/subsystems/transport.md`` § "为什么是 Flag，不是 Config".
Choosing a protocol stack is a startup-frozen decision — changing
it mid-run would be meaningless for any live connection — so it
fits the FlagManager model (load once, validate, freeze) rather
than the ConfigManager model (bind, update, signal).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from kernel.routes.stack import StackName


class TransportFlags(BaseModel):
    """``[transport]`` section of ``flags.yaml``.

    Fields
    ------
    stack:
        Name of the :class:`kernel.routes.stack.ProtocolStack` to
        drive the ``/session`` WebSocket loop.  Typed as
        :data:`~kernel.routes.stack.StackName` (a ``Literal``) so
        pydantic rejects unknown names during
        :meth:`kernel.flags.FlagManager.register` — a misspelling
        in ``flags.yaml`` fails kernel boot with a pydantic
        ``ValidationError`` instead of turning into a runtime
        fallback branch in transport code.
    """

    stack: StackName = Field(
        "dummy",
        description=("Registered ProtocolStack name — see kernel.routes.stack.create_stack."),
    )
