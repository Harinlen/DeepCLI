"""LLMProviderManager — LLM Provider instance lifecycle management.

Manages ``Provider`` instances, deduplicating by ``(type, api_key, base_url)``.
Multiple model entries sharing the same credentials share one Provider instance
(and therefore one HTTP connection pool).

This subsystem has no config section of its own.  ``LLMManager`` calls
``get_provider()`` during its ``startup()`` to drive instance creation.
"""

from __future__ import annotations

import logging

from kernel.llm_provider.base import Provider
from kernel.subsystem import Subsystem

logger = logging.getLogger(__name__)

# Dedup key: (provider_type, api_key, base_url)
_CredKey = tuple[str, str | None, str | None, str | None, str | None]
#              (type,  api_key,  base_url, aws_secret, aws_region)


class LLMProviderManager(Subsystem):
    """Caches and manages Provider instances by credentials.

    LLMManager is the only caller.  All other kernel code interacts with
    the LLM layer via LLMManager, never directly with providers.
    """

    async def startup(self) -> None:
        self._providers: dict[_CredKey, Provider] = {}
        logger.info("LLMProviderManager: ready")

    async def shutdown(self) -> None:
        for key, provider in self._providers.items():
            try:
                await provider.aclose()
            except Exception:
                logger.exception("LLMProviderManager: error closing provider %s", key)
        self._providers.clear()
        logger.info("LLMProviderManager: shutdown complete")

    def get_provider(
        self,
        *,
        provider_type: str,
        api_key: str | None,
        base_url: str | None,
        aws_secret_key: str | None = None,
        aws_region: str | None = None,
    ) -> Provider:
        """Return the cached Provider for these credentials, creating if needed."""
        key: _CredKey = (provider_type, api_key, base_url, aws_secret_key, aws_region)
        if key not in self._providers:
            self._providers[key] = _create_provider(
                provider_type=provider_type,
                api_key=api_key,
                base_url=base_url,
                aws_secret_key=aws_secret_key,
                aws_region=aws_region,
            )
            logger.info(
                "LLMProviderManager: created %s provider (base_url=%s)",
                provider_type,
                base_url or "<default>",
            )
        return self._providers[key]


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def _create_provider(
    *,
    provider_type: str,
    api_key: str | None,
    base_url: str | None,
    aws_secret_key: str | None = None,
    aws_region: str | None = None,
) -> Provider:
    match provider_type:
        case "anthropic":
            from kernel.llm_provider.anthropic import AnthropicProvider

            return AnthropicProvider(api_key=api_key, base_url=base_url)
        case "bedrock":
            from kernel.llm_provider.bedrock import BedrockProvider

            return BedrockProvider(
                aws_access_key=api_key,
                aws_secret_key=aws_secret_key,
                aws_region=aws_region,
            )
        case "openai_compatible":
            from kernel.llm_provider.openai_compatible import OpenAICompatibleProvider

            return OpenAICompatibleProvider(api_key=api_key, base_url=base_url)
        case "nvidia":
            from kernel.llm_provider.nvidia import NvidiaProvider

            return NvidiaProvider(api_key=api_key, base_url=base_url)
        case _:
            raise ValueError(
                f"Unknown provider type '{provider_type}'. "
                f"Supported: anthropic, bedrock, openai_compatible, nvidia"
            )
