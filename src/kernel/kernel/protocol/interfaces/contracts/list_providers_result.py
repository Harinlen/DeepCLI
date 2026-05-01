"""ListProvidersResult -- contract type returned by model/provider_list."""

from __future__ import annotations

from pydantic import BaseModel



class ProviderInfo(BaseModel):
    """Metadata for one registered provider."""

    name: str
    """User-chosen logical name (e.g. ``"anthropic"``)."""

    provider_type: str
    """Provider backend type (e.g. ``"anthropic"``)."""

    models: list[str]
    """Model IDs available under this provider."""

    roles: dict[str, bool]
    """Role assignments: ``{"default": True, "bash_judge": False, ...}``."""


class ListProvidersResult(BaseModel):
    """Result of a model/provider_list operation."""

    providers: list[ProviderInfo]
    """All registered providers."""

    default_model: list[str]
    """The current default model as ``[provider, model_id]``."""
