"""Protocol layer — ACP over WebSocket.

Public surface
--------------
* :func:`build_protocol_stack` — factory used by ``create_stack``
* :class:`ProtocolFlags` — flag section registered in lifespan
* Re-exports of the protocol-agnostic interface types that other
  subsystems (session layer, tools, …) need to reference without
  importing the ACP sub-package directly.
"""

from typing import TYPE_CHECKING

from kernel.core.protocol.flags import ProtocolFlags

if TYPE_CHECKING:
    from kernel.agents.mustang.module_table import KernelModuleTable
from kernel.core.protocol.interfaces.client_sender import ClientSender
from kernel.core.protocol.interfaces.contracts.connection_context import (
    ConnectionContext,
)
from kernel.core.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.core.protocol.interfaces.errors import ProtocolError
from kernel.core.protocol.interfaces.session_handler import SessionHandler

__all__ = [
    "ClientSender",
    "ConnectionContext",
    "HandlerContext",
    "ProtocolError",
    "ProtocolFlags",
    "SessionHandler",
    "build_protocol_stack",
]


def build_protocol_stack(module_table: "KernelModuleTable") -> object:
    """Build the ACP :class:`~kernel.agents.access.routes.stack.ProtocolStack`.

    Imported lazily so that the heavy ACP sub-package is only loaded
    when the kernel actually uses it (i.e. not in tests that mock the
    stack).
    """
    from kernel.core.protocol.acp import build_acp_stack

    return build_acp_stack(module_table)
