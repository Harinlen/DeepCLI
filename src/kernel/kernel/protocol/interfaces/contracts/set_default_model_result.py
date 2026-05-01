"""SetDefaultModelResult -- contract type returned by model/set_default."""

from __future__ import annotations

from pydantic import BaseModel


class SetDefaultModelResult(BaseModel):
    """Result of a model/set_default operation."""

    default_model: list[str]
    """The resolved model ref now set as the kernel default, as ``[provider, model_id]``."""
