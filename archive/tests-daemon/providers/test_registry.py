"""Tests for provider registry — registration, lookup, model resolution."""

import logging

import pytest

from daemon.config.defaults import apply_defaults
from daemon.config.schema import ProviderSourceConfig, SourceConfig
from daemon.errors import ProviderNotFoundError
from daemon.providers.registry import ProviderRegistry


@pytest.fixture
def registry() -> ProviderRegistry:
    """Registry built from default config."""
    config = apply_defaults(SourceConfig())
    return ProviderRegistry.from_config(config)


class TestProviderRegistry:
    def test_default_provider(self, registry: ProviderRegistry):
        assert registry.default.name == "local"

    def test_get_existing_provider(self, registry: ProviderRegistry):
        p = registry.get("local")
        assert p.name == "local"

    def test_get_nonexistent_provider_raises(self, registry: ProviderRegistry):
        with pytest.raises(ProviderNotFoundError, match="not registered"):
            registry.get("nonexistent")

    def test_provider_names(self, registry: ProviderRegistry):
        assert "local" in registry.provider_names

    def test_resolve_alias(self, registry: ProviderRegistry):
        provider, model = registry.resolve_model("qwen")
        assert provider.name == "local"
        assert model == "qwen3.5"

    def test_resolve_provider_slash_model(self, registry: ProviderRegistry):
        provider, model = registry.resolve_model("local/some-model")
        assert provider.name == "local"
        assert model == "some-model"

    def test_resolve_bare_model(self, registry: ProviderRegistry):
        """Bare model name uses default provider."""
        provider, model = registry.resolve_model("any-model")
        assert provider.name == "local"
        assert model == "any-model"

    def test_resolve_unknown_provider_raises(self, registry: ProviderRegistry):
        with pytest.raises(ProviderNotFoundError):
            registry.resolve_model("unknown_provider/model")

    def test_minimax_type_creates_minimax_provider(self):
        """type: 'minimax' creates MiniMaxProvider."""
        from daemon.providers.minimax import MiniMaxProvider

        source = SourceConfig(
            provider={
                "default": "mm",
                "mm": ProviderSourceConfig(
                    type="minimax",
                    base_url="https://api.minimax.io/v1",
                    model="MiniMax-M2.7",
                    api_key="test",
                ),
            }
        )
        config = apply_defaults(source)
        registry = ProviderRegistry.from_config(config)
        assert isinstance(registry.get("mm"), MiniMaxProvider)

    def test_migration_warning_for_minimax_url(self, caplog: pytest.LogCaptureFixture):
        """Warns when type=openai_compatible but URL is MiniMax."""
        source = SourceConfig(
            provider={
                "default": "mm",
                "mm": ProviderSourceConfig(
                    type="openai_compatible",
                    base_url="https://api.minimax.io/v1",
                    model="MiniMax-M2.7",
                    api_key="test",
                ),
            }
        )
        config = apply_defaults(source)
        with caplog.at_level(logging.WARNING):
            ProviderRegistry.from_config(config)

        assert any("Consider changing to type: 'minimax'" in r.message for r in caplog.records)
