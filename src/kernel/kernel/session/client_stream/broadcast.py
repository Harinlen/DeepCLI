"""Push ``session/update`` notifications to every connection observing a session.

Senders that fail mid-broadcast are dropped — a closed WebSocket should not
prevent siblings from receiving the update.
"""

from __future__ import annotations

import logging
from typing import Any

from kernel.protocol.acp.schemas.updates import SessionUpdateNotification
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.runtime.state import Session

logger = logging.getLogger("kernel.session")


class SessionBroadcastMixin(_SessionMixinBase):
    """Fans ``session/update`` notifications out to every connected sender."""

    async def _broadcast(self, session: Session, update: Any) -> None:
        """Fan one ``session/update`` notification out to every observer.

        Args:
            session: Owning session whose ``senders`` are notified.
            update: Update payload (any ACP ``SessionUpdate`` variant) to
                wrap in a ``SessionUpdateNotification``.

        Senders that raise during ``notify`` are dropped from the session
        — a half-closed WebSocket must not block siblings.
        """
        if not session.senders:
            return
        notification = SessionUpdateNotification(
            session_id=session.session_id,
            update=update,
        )
        dead: list[str] = []
        for conn_id, sender in list(session.senders.items()):
            try:
                await sender.notify("session/update", notification)
            except Exception:
                logger.exception(
                    "session=%s conn=%s: notify failed — removing",
                    session.session_id,
                    conn_id,
                )
                dead.append(conn_id)
        for conn_id in dead:
            session.senders.pop(conn_id, None)

