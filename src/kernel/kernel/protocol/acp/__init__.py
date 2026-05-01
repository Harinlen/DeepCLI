"""ACP protocol stack implementation.

Entry point: :func:`build_acp_stack`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kernel.module_table import KernelModuleTable
    from kernel.routes.stack import ProtocolStack


def build_acp_stack(module_table: KernelModuleTable) -> ProtocolStack[Any]:
    """Construct the ACP ``(codec, dispatcher)`` pair.

    The stack is stateless at the codec level and stateful at the
    dispatcher level (each connection gets its own
    :class:`~kernel.protocol.acp.session_handler.AcpSessionHandler`).
    """
    from kernel.protocol.acp.codec import AcpCodec
    from kernel.protocol.acp.session_handler import AcpSessionHandler
    from kernel.routes.stack import ProtocolStack

    return ProtocolStack(
        codec=AcpCodec(),
        dispatcher=AcpSessionHandler(module_table),
    )
