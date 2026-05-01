"""Provider registry — factory map, model alias resolution, provider lookup.

New provider implementations register themselves in the module-level
``PROVIDER_TYPES`` factory map.  ``ProviderRegistry.from_config()``
looks up the ``type`` field from each provider's config to choose which
class to instantiate — no hardcoded if-else chains.

To add a new provider:
  1. Create a ``Provider`` subclass (e.g. ``AnthropicProvider``).
  2. Register it: ``PROVIDER_TYPES["anthropic"] = AnthropicProvider``
     (typically done at import time in the new module, or here).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from daemon.config.schema import ProviderRuntimeConfig, RuntimeConfig
from daemon.errors import ProviderNotFoundError
from daemon.providers.anthropic import AnthropicProvider
from daemon.providers.base import Provider
from daemon.providers.bedrock import BedrockProvider
from daemon.providers.minimax import MiniMaxProvider
from daemon.providers.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

# Factory signature: (config, provider_name) → Provider
ProviderFactory = Callable[[ProviderRuntimeConfig, str], Provider]

# Factory map: provider type name → factory function.
# Add new provider implementations here (or call register_provider_type).
PROVIDER_TYPES: dict[str, ProviderFactory] = {
    "openai_compatible": lambda cfg, name: OpenAICompatibleProvider(cfg, provider_name=name),
    "minimax": lambda cfg, name: MiniMaxProvider(cfg, provider_name=name),
    "anthropic": lambda cfg, name: AnthropicProvider(cfg, provider_name=name),
    "bedrock": lambda cfg, name: BedrockProvider(cfg, provider_name=name),
}


def register_provider_type(type_name: str, factory: ProviderFactory) -> None:
    """Register a provider implementation for use in config.

    Args:
        type_name: The ``type`` value in config that selects this provider.
        factory: Callable ``(config, provider_name) → Provider``.
    """
    PROVIDER_TYPES[type_name] = factory


# Short aliases → (provider_name, model_id)
MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "qwen": ("local", "qwen3.5"),
    "qwen3.5": ("local", "qwen3.5"),
    "opus": ("anthropic", "claude-opus-4-20250514"),
    "sonnet": ("anthropic", "claude-sonnet-4-20250514"),
    "haiku": ("anthropic", "claude-haiku-4-20250514"),
}


# base_url patterns → recommended provider type.
# Used to warn when type is "openai_compatible" but URL matches a cloud provider.
_KNOWN_PROVIDER_URLS: dict[str, str] = {
    "api.minimax.io": "minimax",
    "api.anthropic.com": "anthropic",
}


def _create_provider(
    name: str,
    config: ProviderRuntimeConfig,
) -> Provider | None:
    """Instantiate a provider from config using the factory map.

    Returns ``None`` if the type is unknown (logged as warning).
    Emits a migration warning if the base_url matches a known cloud
    provider but the type is still ``"openai_compatible"``.

    Args:
        name: Logical provider name (e.g. ``"local"``).
        config: Resolved provider config with ``type`` field.

    Returns:
        Provider instance, or None on unknown type.
    """
    # Migration hint: detect mismatched type
    if config.type == "openai_compatible":
        for url_pattern, suggested_type in _KNOWN_PROVIDER_URLS.items():
            if url_pattern in config.base_url:
                logger.warning(
                    "Provider '%s' uses base_url containing '%s' but type is "
                    "'openai_compatible'. Consider changing to type: '%s' for "
                    "better context window detection and provider-specific features.",
                    name,
                    url_pattern,
                    suggested_type,
                )
                break

    factory = PROVIDER_TYPES.get(config.type)
    if factory is None:
        logger.warning(
            "Unknown provider type %r for '%s' — available: %s. Skipping.",
            config.type,
            name,
            list(PROVIDER_TYPES.keys()),
        )
        return None
    return factory(config, name)


class ProviderRegistry:
    """Manages provider instances and resolves model aliases to providers."""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}
        self._default_provider: str = "local"

    @classmethod
    def from_config(cls, config: RuntimeConfig) -> ProviderRegistry:
        """Build registry from RuntimeConfig, instantiating all providers.

        Uses the factory map to choose the right class based on each
        provider's ``type`` field.
        """
        registry = cls()
        registry._default_provider = config.default_provider

        for name, provider_config in config.providers.items():
            provider = _create_provider(name, provider_config)
            if provider is None:
                continue
            registry.register(provider)
            logger.info(
                "Registered provider '%s' (type=%s) → %s (model: %s)",
                name,
                provider_config.type,
                provider_config.base_url,
                provider_config.model,
            )

        return registry

    def register(self, provider: Provider) -> None:
        """Add a provider instance, keyed by ``provider.name``."""
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider:
        """Look up a provider by name.

        Raises:
            ProviderNotFoundError: If no provider with that name exists.
        """
        if name not in self._providers:
            raise ProviderNotFoundError(
                f"Provider '{name}' not registered. Available: {list(self._providers)}"
            )
        return self._providers[name]

    @property
    def default(self) -> Provider:
        """The default provider as configured."""
        return self.get(self._default_provider)

    def resolve_model(self, model_ref: str) -> tuple[Provider, str]:
        """Resolve alias/provider-slash-model/bare-model to (Provider, model_id).

        Raises:
            ProviderNotFoundError: If the resolved provider doesn't exist.
        """
        if model_ref in MODEL_ALIASES:
            provider_name, model_id = MODEL_ALIASES[model_ref]
            return self.get(provider_name), model_id

        if "/" in model_ref:
            provider_name, model_id = model_ref.split("/", 1)
            return self.get(provider_name), model_id

        return self.default, model_ref

    @property
    def provider_names(self) -> list[str]:
        """Names of all registered providers."""
        return list(self._providers.keys())
