"""Parameters for official ACP ``session/resume``."""

from __future__ import annotations

from pydantic import BaseModel


class ResumeSessionParams(BaseModel):
    """Reattach to a session without replaying its transcript."""

    session_id: str
    cwd: str | None = None
