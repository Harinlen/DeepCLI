"""ListProvidersResult -- contract type returned by model/provider_list."""

from __future__ import annotations

from pydantic import BaseModel


class ProviderInfo(BaseModel):
    """Metadata for one registered provider."""

    name: str
    """User-chosen logical name (e.g. ``"anthropic"``)."""

    provider_type: str
    """Provider backend type (e.g. ``"anthropic"``)."""

    base_url: str | None = None
    """Configured provider base URL, if any."""

    effective_base_url: str | None = None
    """Effective provider base URL after applying provider defaults."""

    aws_region: str | None = None
    """Configured AWS region for Bedrock-style providers, if any."""

    has_api_key: bool = False
    """Whether an API key/access key is configured without exposing it."""

    api_key_display: str | None = None
    """Raw API key/access key for local user-facing configuration UIs."""

    has_aws_secret_key: bool = False
    """Whether an AWS secret key is configured without exposing it."""

    aws_secret_key_display: str | None = None
    """Raw AWS secret key for local user-facing configuration UIs."""

    setting_fields: list[str]
    """Provider setting fields the UI should expose for this provider type."""

    models: list[str]
    """Model IDs available under this provider."""

    context_windows: dict[str, int]
    """Context windows by model id.  Values include kernel fallback defaults."""

    display_names: dict[str, str]
    """Optional user-facing display names by model id."""

    roles: dict[str, bool]
    """Role assignments: ``{"default": True, "bash_judge": False, ...}``."""


class ProviderTypeInfo(BaseModel):
    """UI metadata for one supported provider backend type."""

    provider_type: str
    """Provider backend type."""

    setting_fields: list[str]
    """Provider setting fields the UI should expose for this type."""

    effective_base_url: str | None = None
    """Default/effective base URL for this type, if any."""


class ListProvidersResult(BaseModel):
    """Result of a model/provider_list operation."""

    providers: list[ProviderInfo]
    """All registered providers."""

    provider_type_options: list[ProviderTypeInfo]
    """All provider backend types supported by this kernel."""

    current_used: dict[str, list[str]]
    """Current-used role assignments as ``role -> [provider, model_id]``."""

    default_context_window: int
    """Kernel fallback context window used when a provider has no exact value."""
