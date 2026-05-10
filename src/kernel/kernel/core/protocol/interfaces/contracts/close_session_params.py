"""Parameters for official ACP ``session/close``."""

from __future__ import annotations

from pydantic import BaseModel


class CloseSessionParams(BaseModel):
    """Release active runtime resources while preserving durable state."""

    session_id: str
