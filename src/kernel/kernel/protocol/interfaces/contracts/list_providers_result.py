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

    context_windows: dict[str, int]
    """Context windows by model id.  Values include kernel fallback defaults."""

    display_names: dict[str, str]
    """Optional user-facing display names by model id."""

    roles: dict[str, bool]
    """Role assignments: ``{"default": True, "bash_judge": False, ...}``."""


class ListProvidersResult(BaseModel):
    """Result of a model/provider_list operation."""

    providers: list[ProviderInfo]
    """All registered providers."""

    current_used: dict[str, list[str]]
    """Current-used role assignments as ``role -> [provider, model_id]``."""

    default_context_window: int
    """Kernel fallback context window used when a provider has no exact value."""
