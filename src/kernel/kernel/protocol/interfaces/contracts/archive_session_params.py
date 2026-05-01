"""Parameters for archiving or unarchiving a session."""

from __future__ import annotations

from pydantic import BaseModel


class ArchiveSessionParams(BaseModel):
    """Input to ``session/archive``."""

    session_id: str
    archived: bool = True
