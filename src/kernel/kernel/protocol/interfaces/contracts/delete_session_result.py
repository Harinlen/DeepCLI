"""Result of deleting a session."""

from __future__ import annotations

from pydantic import BaseModel


class DeleteSessionResult(BaseModel):
    """Output from ``session/delete``."""

    deleted: bool
