"""Result of listing sessions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SessionSummary(BaseModel):
    """Lightweight metadata about a single session."""

    session_id: str
    cwd: str
    updated_at: str
    """ISO-8601 UTC timestamp used by ACP session/list."""
    title: str | None = None
    meta: dict[str, object] | None = None
    archived_at: str | None = None
    title_source: Literal["auto", "user"] | None = None
    created_at: str | None = None
    """Internal/backward-compatible timestamp; not emitted on ACP list by default."""
    message_count: int = 0
    """Number of persisted user/assistant messages in the session."""
    turn_count: int = 0
    """Number of completed LLM turns in the session."""


class ListSessionsResult(BaseModel):
    """Output from :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.list`."""

    sessions: list[SessionSummary]
    next_cursor: str | None = None
    """If present, pass this back as ``cursor`` to fetch the next page."""
