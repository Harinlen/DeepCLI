"""RefreshModelsResult -- contract type returned by model/provider_refresh."""

from __future__ import annotations

from pydantic import BaseModel


class RefreshModelsResult(BaseModel):
    """Result of a successful model/provider_refresh operation."""

    models: list[str]
    """The updated model IDs after discovery."""
