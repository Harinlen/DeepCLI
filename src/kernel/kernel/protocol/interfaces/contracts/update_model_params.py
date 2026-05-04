"""UpdateModelParams -- contract type for model/update."""

from __future__ import annotations

from pydantic import BaseModel

from kernel.llm.config import ModelRef


class UpdateModelParams(BaseModel):
    """Parameters for updating one provider model's user-facing settings."""

    model: ModelRef
    """Provider/model ref to update."""

    display_name: str | None = None
    """Optional user-facing name.  ``None`` resets to the raw model id."""

    context_window: int | None = None
    """Optional context-window override in tokens."""

    roles: list[str] | None = None
    """If provided, exact current-used roles assigned to this model."""
