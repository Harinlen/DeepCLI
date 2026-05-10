"""UpdateModelResult -- contract type returned by model/update."""

from __future__ import annotations

from pydantic import BaseModel


class UpdateModelResult(BaseModel):
    """Result of a successful model/update operation."""

    model: list[str]
    """Updated model ref as ``[provider, model_id]``."""

    provider_type: str
    """Persisted provider backend type."""

    base_url: str | None
    """Persisted provider base URL, if any."""

    effective_base_url: str | None
    """Effective provider base URL after applying provider defaults."""

    aws_region: str | None
    """Persisted AWS region, if any."""

    has_api_key: bool
    """Whether an API key/access key is configured."""

    api_key_display: str | None
    """Raw API key/access key for local user-facing configuration UIs."""

    has_aws_secret_key: bool
    """Whether an AWS secret key is configured."""

    aws_secret_key_display: str | None
    """Raw AWS secret key for local user-facing configuration UIs."""

    setting_fields: list[str]
    """Provider setting fields the UI should expose for this provider type."""

    display_name: str | None
    """Persisted display-name override, if any."""

    context_window: int | None
    """Persisted context-window override, if any."""

    roles: list[str]
    """Current-used roles now assigned to this model."""
