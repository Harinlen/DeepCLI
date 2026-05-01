"""Session subsystem public API."""

from __future__ import annotations

from kernel.session.context import AgentContext
from kernel.session.runtime.flags import SessionFlags
from kernel.session.runtime.helpers import make_summarise_closure as _make_summarise_closure
from kernel.session.manager import SessionManager
from kernel.session.runtime.state import QueuedTurn, Session, TurnState

__all__ = [
    "QueuedTurn",
    "AgentContext",
    "Session",
    "SessionFlags",
    "SessionManager",
    "TurnState",
    "_make_summarise_closure",
]
