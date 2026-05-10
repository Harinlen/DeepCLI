"""RemoveProfileParams — contract type for model/profile_remove."""

from __future__ import annotations

from pydantic import BaseModel


class RemoveProfileParams(BaseModel):
    """Parameters for removing a registered LLM model profile."""

    name: str
    """Logical name of the profile to remove."""
