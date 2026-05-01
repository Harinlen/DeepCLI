"""Tests for AnthropicProvider construction + fallback context window."""

from __future__ import annotations

import pytest

from daemon.config.schema import ProviderRuntimeConfig
from daemon.providers.anthropic import AnthropicProvider, _lookup_context_window


@pytest.fixture
def anthropic_config() -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        type="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4-20250514",
        api_key="sk-ant-test",
    )


class TestAnthropicProvider:
    def test_name(self, anthropic_config: ProviderRuntimeConfig) -> None:
        p = AnthropicProvider(anthropic_config, provider_name="claude")
        assert p.name == "claude"

    def test_default_model(self, anthropic_config: ProviderRuntimeConfig) -> None:
        p = AnthropicProvider(anthropic_config)
        assert p._default_model == "claude-sonnet-4-20250514"

    def test_registry_integration(self, anthropic_config: ProviderRuntimeConfig) -> None:
        from daemon.providers.registry import PROVIDER_TYPES

        factory = PROVIDER_TYPES["anthropic"]
        provider = factory(anthropic_config, "claude")
        assert provider.name == "claude"


class TestLookupContextWindow:
    def test_known_opus(self) -> None:
        assert _lookup_context_window("claude-opus-4-20250514") == 200_000

    def test_known_sonnet(self) -> None:
        assert _lookup_context_window("claude-sonnet-4-20250514") == 200_000

    def test_known_haiku_3_5(self) -> None:
        assert _lookup_context_window("claude-3-5-haiku-20241022") == 200_000

    def test_unknown_returns_none(self) -> None:
        assert _lookup_context_window("gpt-5-hypothetical") is None
