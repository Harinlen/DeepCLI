"""AddProfileParams — contract type for model/profile_add."""

from __future__ import annotations

from pydantic import BaseModel


class AddProfileParams(BaseModel):
    """Parameters for adding a new LLM model profile at runtime."""

    name: str
    """User-chosen logical name for the profile (e.g. ``"my-qwen"``)."""

    provider_type: str
    """Provider backend: ``"anthropic"`` | ``"bedrock"`` | ``"openai_compatible"``."""

    model_id: str
    """Actual API model identifier sent to the provider (e.g. ``"qwen3-32b"``)."""

    base_url: str | None = None
    """Custom endpoint URL.  ``None`` uses the provider default."""

    api_key: str | None = None
    """API key.  Not required for ``bedrock`` (uses AWS credential chain)."""

    max_tokens: int = 8192
    """Maximum tokens to request per completion."""

    thinking: bool = False
    """Enable extended thinking / reasoning (Anthropic only)."""

    prompt_caching: bool = True
    """Enable prompt caching where supported (Anthropic only)."""
