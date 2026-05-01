"""ProtocolFlags — startup-time switch for which protocol stack to use.

Registered by the kernel lifespan immediately after FlagManager
initialises (same pattern as ``TransportFlags`` — protocol is not a
Subsystem, so it registers its own flags directly in ``app.py``).

Adding a new protocol implementation
--------------------------------------
1. Implement codec + dispatcher in ``kernel/protocol/<name>/``.
2. Add the name to the ``Literal`` type below (mypy will catch any
   missing ``create_stack`` branch at static-analysis time).
3. Add a branch in ``kernel/routes/stack/__init__.py``
   ``create_stack`` factory.

The ``Literal`` + pydantic validation means a typo in ``flags.yaml``
aborts kernel startup with a clear ``ValidationError`` rather than
surfacing as a runtime guard inside the transport loop.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProtocolFlags(BaseModel):
    """Flag section ``"protocol"`` in ``~/.mustang/flags.yaml``."""

    implementation: Literal["acp"] = Field(
        "acp",
        description=(
            "Protocol codec + dispatcher implementation to use for "
            "the /session WebSocket endpoint.  Currently only 'acp' "
            "is supported.  Future values: 'xyz', etc."
        ),
    )
