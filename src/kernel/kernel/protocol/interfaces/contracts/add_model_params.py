"""AddModelParams -- contract type for model/add."""

from __future__ import annotations

from pydantic import BaseModel


class AddModelParams(BaseModel):
    """Parameters for adding one model to an existing or new provider."""

    provider_name: str
    """Target provider name. Existing providers are reused."""

    provider_type: str | None = None
    """Provider backend type. Required when creating a new provider."""

    api_key: str | None = None
    """API key/access key for a new provider."""

    base_url: str | None = None
    """Base URL for a new provider. Empty string clears to default."""

    aws_secret_key: str | None = None
    """AWS secret key for a new Bedrock-style provider."""

    aws_region: str | None = None
    """AWS region for a new Bedrock-style provider."""

    model_id: str
    """Model id to add under the provider."""

    display_name: str | None = None
    """Optional user-facing model name."""

    context_window: int | None = None
    """Optional context-window override in tokens."""

    roles: list[str] | None = None
    """If provided, current-used roles assigned to this model."""
