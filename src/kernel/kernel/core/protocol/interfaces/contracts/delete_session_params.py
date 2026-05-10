"""Parameters for deleting a session."""

from __future__ import annotations

from pydantic import BaseModel


class DeleteSessionParams(BaseModel):
    """Input to ``session/delete``."""

    session_id: str
    force: bool = False
