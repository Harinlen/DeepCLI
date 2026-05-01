"""RefreshModelsParams -- contract type for model/provider_refresh."""

from __future__ import annotations

from pydantic import BaseModel


class RefreshModelsParams(BaseModel):
    """Parameters for refreshing the model list of a provider."""

    name: str
    """Logical name of the provider to refresh."""
