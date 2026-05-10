"""AddProviderResult -- contract type returned by model/provider_add."""

from __future__ import annotations

from pydantic import BaseModel


class AddProviderResult(BaseModel):
    """Result of a successful model/provider_add operation."""

    name: str
    """The logical name of the newly added provider."""

    models: list[str]
    """The model IDs available under this provider (discovered or explicit)."""
