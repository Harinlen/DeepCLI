"""Result for official ACP ``session/close``."""

from __future__ import annotations

from pydantic import BaseModel


class CloseSessionResult(BaseModel):
    """Empty success result for ``session/close``."""
