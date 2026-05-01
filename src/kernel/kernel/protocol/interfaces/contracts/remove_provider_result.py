"""RemoveProviderResult -- contract type returned by model/provider_remove."""

from __future__ import annotations

from pydantic import BaseModel


class RemoveProviderResult(BaseModel):
    """Result of a successful model/provider_remove operation."""
