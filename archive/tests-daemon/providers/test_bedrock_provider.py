"""Tests for BedrockProvider construction + fallback context window."""

from __future__ import annotations

import pytest

from daemon.config.schema import ProviderRuntimeConfig
from daemon.providers.bedrock import BedrockProvider, _lookup_context_window


@pytest.fixture
def bedrock_config() -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        type="bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        model="anthropic.claude-sonnet-4-6",
        api_key="AKIAIOSFODNN7EXAMPLE",
        aws_secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        aws_region="us-east-1",
    )


class TestBedrockProvider:
    def test_name(self, bedrock_config: ProviderRuntimeConfig) -> None:
        p = BedrockProvider(bedrock_config, provider_name="bedrock")
        assert p.name == "bedrock"

    def test_default_model(self, bedrock_config: ProviderRuntimeConfig) -> None:
        p = BedrockProvider(bedrock_config)
        assert p._default_model == "anthropic.claude-sonnet-4-6"

    def test_registry_integration(self, bedrock_config: ProviderRuntimeConfig) -> None:
        from daemon.providers.registry import PROVIDER_TYPES

        factory = PROVIDER_TYPES["bedrock"]
        provider = factory(bedrock_config, "bedrock")
        assert provider.name == "bedrock"


class TestLookupContextWindow:
    def test_known_sonnet(self) -> None:
        assert _lookup_context_window("anthropic.claude-sonnet-4-6") == 200_000

    def test_cross_region_prefix(self) -> None:
        assert _lookup_context_window("us.anthropic.claude-sonnet-4-6") == 200_000

    def test_global_prefix(self) -> None:
        assert _lookup_context_window("global.anthropic.claude-sonnet-4-6") == 200_000

    def test_known_opus(self) -> None:
        assert _lookup_context_window("anthropic.claude-opus-4-20250514") == 200_000

    def test_unknown_returns_none(self) -> None:
        assert _lookup_context_window("amazon.titan-text-express") is None
