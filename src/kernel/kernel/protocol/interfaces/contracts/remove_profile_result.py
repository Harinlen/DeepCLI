"""RemoveProfileResult — contract type returned by model/profile_remove."""

from __future__ import annotations

from pydantic import BaseModel


class RemoveProfileResult(BaseModel):
    """Result of a successful model/profile_remove operation."""
