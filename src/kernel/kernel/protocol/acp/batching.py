"""ACP-specific batcher configuration.

Instantiates a :class:`~kernel.protocol.interfaces.batcher.Batcher`
with ACP-specific ``is_mergeable`` and ``merge`` functions.

Per ``docs/interfaces/protocol.md``:

Mergeable variants (only ``*_chunk`` with ``type == "text"``):
* ``agent_message_chunk``
* ``agent_thought_chunk``
* ``user_message_chunk``

All other session/update variants are not mergeable and flush the
buffer immediately before being sent.

Merge semantics: concatenate ``content.text`` of consecutive chunks of
the same variant.  Different variants are NOT merged with each other
(an ``agent_message_chunk`` and an ``agent_thought_chunk`` stay
separate even if adjacent).
"""

from __future__ import annotations

from typing import Callable

from kernel.protocol.acp.schemas.updates import (
    AgentMessageChunk,
    AgentThoughtChunk,
    SessionUpdateNotification,
    UserMessageChunk,
)
from kernel.protocol.acp.schemas.content import AcpTextBlock
from kernel.protocol.interfaces.batcher import Batcher

# The three mergeable session/update variant types.
_MERGEABLE_TYPES = (AgentMessageChunk, AgentThoughtChunk, UserMessageChunk)


def _is_mergeable(notif: SessionUpdateNotification) -> bool:
    """Return ``True`` iff this notification's update can be coalesced."""
    update = notif.update
    if not isinstance(update, _MERGEABLE_TYPES):
        return False
    # Only text blocks are mergeable; image/resource blocks go direct.
    return isinstance(update.content, AcpTextBlock)


def _merge(
    a: SessionUpdateNotification,
    b: SessionUpdateNotification,
) -> SessionUpdateNotification:
    """Concatenate the text of two same-variant chunk notifications.

    Precondition (enforced by caller via ``is_mergeable``): both
    notifications have the same ``sessionUpdate`` discriminator value
    and text content blocks.
    """
    a_update = a.update
    b_update = b.update
    # Both are text blocks — concatenate.
    merged_text = a_update.content.text + b_update.content.text  # type: ignore[union-attr]
    # Rebuild the update with merged text, preserving the variant type.
    merged_content = AcpTextBlock(text=merged_text)
    merged_update = type(a_update)(content=merged_content)  # type: ignore[call-arg,arg-type]
    return SessionUpdateNotification(
        session_id=a.session_id,
        update=merged_update,
    )


def make_acp_batcher(
    send: Callable[[SessionUpdateNotification], object],
    window_ms: float = 50.0,
) -> Batcher[SessionUpdateNotification]:
    """Return a :class:`~kernel.protocol.interfaces.batcher.Batcher`
    configured for ACP session/update coalescing.

    Parameters
    ----------
    send:
        Coroutine that delivers one ``SessionUpdateNotification``
        (typically ``lambda n: sender.notify("session/update", n)``).
    window_ms:
        Coalescing window in milliseconds.  Defaults to 50 ms
        (≈ 20 fps — smooth for humans).  Overridable via Config
        ``protocol.batching.chunk_window_ms`` in production.
    """
    return Batcher(
        send=send,
        is_mergeable=_is_mergeable,
        merge=_merge,
        window_ms=window_ms,
    )
