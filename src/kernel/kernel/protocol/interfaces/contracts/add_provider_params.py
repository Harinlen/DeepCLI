"""AddProviderParams -- contract type for model/provider_add."""

from __future__ import annotations

from pydantic import BaseModel


class AddProviderParams(BaseModel):
    """Parameters for adding a new LLM provider at runtime."""

    name: str
    """User-chosen logical name for the provider (e.g. ``"bedrock"``)."""

    provider_type: str
    """Provider backend: ``"anthropic"`` | ``"bedrock"`` | ``"openai_compatible"`` | ``"nvidia"``."""

    api_key: str | None = None
    """API key.  For ``bedrock``: AWS access key ID."""

    base_url: str | None = None
    """Custom endpoint URL.  ``None`` uses the provider default."""

    aws_secret_key: str | None = None
    """AWS secret access key.  ``bedrock`` only."""

    aws_region: str | None = None
    """AWS region (e.g. ``"us-east-1"``).  ``bedrock`` only."""

    models: list[str] | None = None
    """Explicit model list.  Required for ``bedrock`` (no auto-discovery).
    ``None`` for other providers triggers auto-discovery."""
