"""RemoveProviderParams -- contract type for model/provider_remove."""

from __future__ import annotations

from pydantic import BaseModel


class RemoveProviderParams(BaseModel):
    """Parameters for removing a registered LLM provider."""

    name: str
    """Logical name of the provider to remove."""
