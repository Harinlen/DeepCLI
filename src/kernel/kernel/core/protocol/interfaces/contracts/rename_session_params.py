"""Parameters for renaming a session."""

from __future__ import annotations

from pydantic import BaseModel


class RenameSessionParams(BaseModel):
    """Input to ``session/rename``."""

    session_id: str
    title: str
