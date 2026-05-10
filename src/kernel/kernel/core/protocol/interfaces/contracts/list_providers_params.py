"""ListProvidersParams -- contract type for model/provider_list."""

from __future__ import annotations

from pydantic import BaseModel


class ListProvidersParams(BaseModel):
    """Parameters for listing all registered LLM providers."""
