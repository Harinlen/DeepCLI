"""Runtime state for a single active session.

A :class:`Session` owns the per-connection infrastructure: an
:class:`Orchestrator` (which owns the :class:`Conversation`), a
:class:`TranscriptWriter` for JSONL persistence, and the set of
WebSocket connections subscribed to this session for event
broadcasting.

Concurrency model:

- Each session has an ``asyncio.Lock`` (``query_lock``) so at most
  one LLM query executes at a time.  A second ``user_message``
  from another connection waits for the lock rather than being
  rejected.
- :meth:`broadcast` sends to every connection; :meth:`send_to`
  unicasts to one (used for ``permission_request``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from daemon.sessions.entry import BaseEntry
from daemon.sessions.storage import TranscriptWriter

if TYPE_CHECKING:
    from daemon.engine.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class Session:
    """Runtime state for a single active session.

    Owns an :class:`Orchestrator` (and therefore a
    :class:`Conversation`), a :class:`TranscriptWriter` for
    persistence, and a set of subscribed WebSocket connections for
    event broadcasting.

    Args:
        session_id: Unique session identifier.
        orchestrator: The LLM query engine for this session.
        writer: Transcript persistence backend.
    """

    def __init__(
        self,
        session_id: str,
        orchestrator: Orchestrator,
        writer: TranscriptWriter,
    ) -> None:
        self.session_id = session_id
        self.orchestrator = orchestrator
        self.writer = writer
        self.connections: set[WebSocket] = set()
        self.query_lock = asyncio.Lock()

    # -- Connection management ---------------------------------------

    def add_connection(self, ws: WebSocket) -> None:
        """Register a WebSocket connection to receive broadcasts."""
        self.connections.add(ws)
        logger.info(
            "Session %s: connection added (total=%d)",
            self.session_id[:8],
            len(self.connections),
        )

    def remove_connection(self, ws: WebSocket) -> None:
        """Unregister a WebSocket connection.

        The session remains alive even when all connections are gone
        (idle session) — it can be resumed later.
        """
        self.connections.discard(ws)
        logger.info(
            "Session %s: connection removed (remaining=%d)",
            self.session_id[:8],
            len(self.connections),
        )

    @property
    def connection_count(self) -> int:
        """Number of active WebSocket connections."""
        return len(self.connections)

    # -- Broadcasting ------------------------------------------------

    async def broadcast(self, event: Any) -> None:
        """Send a stream event to all connected clients.

        Disconnected clients are silently removed from the set.

        Args:
            event: A Pydantic ``StreamEvent`` model with
                ``model_dump()``.
        """
        if not self.connections:
            return

        data = event.model_dump() if hasattr(event, "model_dump") else event
        dead: list[WebSocket] = []

        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                logger.debug("Removing dead connection from session %s", self.session_id[:8])
                dead.append(ws)

        for ws in dead:
            self.connections.discard(ws)

    async def send_to(self, ws: WebSocket, event: Any) -> None:
        """Send an event to a specific connection (unicast).

        Used for ``permission_request`` which should only go to the
        connection that initiated the query.

        Args:
            ws: Target WebSocket.
            event: A Pydantic model or dict.
        """
        data = event.model_dump() if hasattr(event, "model_dump") else event
        try:
            await ws.send_json(data)
        except Exception:
            logger.debug("Unicast failed, removing connection from session %s", self.session_id[:8])
            self.connections.discard(ws)

    # -- Transcript convenience --------------------------------------

    def write_entry(self, entry: BaseEntry) -> None:
        """Append an entry to the transcript writer."""
        self.writer.append(entry)


__all__ = ["Session"]
