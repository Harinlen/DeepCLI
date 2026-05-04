"""UpdateModelResult -- contract type returned by model/update."""

from __future__ import annotations

from pydantic import BaseModel


class UpdateModelResult(BaseModel):
    """Result of a successful model/update operation."""

    model: list[str]
    """Updated model ref as ``[provider, model_id]``."""

    display_name: str | None
    """Persisted display-name override, if any."""

    context_window: int | None
    """Persisted context-window override, if any."""

    roles: list[str]
    """Current-used roles now assigned to this model."""
