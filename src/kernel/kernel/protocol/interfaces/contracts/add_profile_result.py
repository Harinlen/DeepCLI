"""AddProfileResult — contract type returned by model/profile_add."""

from __future__ import annotations

from pydantic import BaseModel


class AddProfileResult(BaseModel):
    """Result of a successful model/profile_add operation."""

    name: str
    """The logical name of the newly added profile."""
