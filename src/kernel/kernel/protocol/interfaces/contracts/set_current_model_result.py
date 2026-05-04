"""SetCurrentModelResult -- contract type returned by model/set_current."""

from __future__ import annotations

from pydantic import BaseModel


class SetCurrentModelResult(BaseModel):
    """Result of assigning a model to a current-used role."""

    role: str
    """Role that was updated."""

    model: list[str]
    """The assigned model as ``[provider, model_id]``."""
