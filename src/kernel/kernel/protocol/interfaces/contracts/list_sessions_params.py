"""Parameters for listing existing sessions."""

from __future__ import annotations

from pydantic import BaseModel


class ListSessionsParams(BaseModel):
    """Input to :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.list`."""

    cursor: str | None = None
    """Opaque pagination cursor from a previous response.  Pass as-is;
    never parse or modify it (ACP spec: cursor is opaque to clients)."""

    cwd: str | None = None
    """Optional filter: only return sessions whose ``cwd`` matches
    this absolute path."""

    include_archived: bool = False
    """When true, include archived sessions in the result."""

    archived_only: bool = False
    """When true, return only archived sessions."""
