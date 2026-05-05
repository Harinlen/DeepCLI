"""UpdateModelParams -- contract type for model/update."""

from __future__ import annotations

from pydantic import BaseModel

from kernel.llm.config import ModelRef


class UpdateModelParams(BaseModel):
    """Parameters for updating one provider model's user-facing settings."""

    model: ModelRef
    """Provider/model ref to update."""

    provider_name: str | None = None
    """Optional replacement provider name."""

    provider_type: str | None = None
    """Optional replacement provider backend type."""

    api_key: str | None = None
    """Optional replacement API key/access key."""

    base_url: str | None = None
    """Optional replacement base URL.  Empty string clears it."""

    aws_secret_key: str | None = None
    """Optional replacement AWS secret key."""

    aws_region: str | None = None
    """Optional replacement AWS region.  Empty string clears it."""

    model_id: str | None = None
    """Optional replacement model id within the provider."""

    display_name: str | None = None
    """Optional user-facing name.  ``None`` resets to the raw model id."""

    context_window: int | None = None
    """Optional context-window override in tokens."""

    roles: list[str] | None = None
    """If provided, exact current-used roles assigned to this model."""
