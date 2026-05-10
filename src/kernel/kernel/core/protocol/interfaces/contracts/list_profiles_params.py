"""ListProfilesParams — contract type for model/profile_list."""

from __future__ import annotations

from pydantic import BaseModel


class ListProfilesParams(BaseModel):
    """Parameters for listing all registered LLM model profiles."""
