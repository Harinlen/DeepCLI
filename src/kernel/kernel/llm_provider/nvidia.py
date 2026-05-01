"""NvidiaProvider — NVIDIA NIM API via OpenAI-compatible Chat Completions.

NVIDIA NIM uses the standard OpenAI Chat Completions wire format.
This subclass only overrides the default ``base_url``.

Reference: https://docs.api.nvidia.com/nim/reference/
"""

from __future__ import annotations

from kernel.llm_provider.openai_compatible import OpenAICompatibleProvider

_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NvidiaProvider(OpenAICompatibleProvider):
    """NVIDIA NIM backend.

    Identical to ``OpenAICompatibleProvider`` except the default
    ``base_url`` points to NVIDIA's hosted NIM endpoint.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url or _DEFAULT_BASE_URL,
        )
