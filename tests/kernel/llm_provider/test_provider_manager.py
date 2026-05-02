from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kernel.llm_provider import LLMProviderManager, _create_provider
from kernel.llm_provider.deepseek import DeepSeekProvider
from kernel.llm_provider.nvidia import NvidiaProvider
from kernel.llm_provider.openai_compatible import OpenAICompatibleProvider


async def test_manager_caches_by_full_credential_tuple() -> None:
    manager = LLMProviderManager.__new__(LLMProviderManager)
    await manager.startup()
    try:
        first = manager.get_provider(
            provider_type="openai_compatible",
            api_key="sk-1",
            base_url="https://one.example/v1",
        )
        same = manager.get_provider(
            provider_type="openai_compatible",
            api_key="sk-1",
            base_url="https://one.example/v1",
        )
        other = manager.get_provider(
            provider_type="openai_compatible",
            api_key="sk-2",
            base_url="https://one.example/v1",
        )

        assert first is same
        assert first is not other
    finally:
        await manager.shutdown()


async def test_shutdown_closes_all_cached_providers_and_keeps_going_on_error() -> None:
    manager = LLMProviderManager.__new__(LLMProviderManager)
    ok = MagicMock()
    ok.aclose = AsyncMock()
    bad = MagicMock()
    bad.aclose = AsyncMock(side_effect=RuntimeError("boom"))
    manager._providers = {
        ("ok", None, None, None, None): ok,
        ("bad", None, None, None, None): bad,
    }

    await manager.shutdown()

    ok.aclose.assert_awaited_once()
    bad.aclose.assert_awaited_once()
    assert manager._providers == {}


def test_create_provider_openai_compatible_vendor_defaults() -> None:
    openai = _create_provider(
        provider_type="openai_compatible",
        api_key="sk-test",
        base_url="https://custom.example/v1",
    )
    nvidia = _create_provider(provider_type="nvidia", api_key="nv-test", base_url=None)
    deepseek = _create_provider(provider_type="deepseek", api_key="ds-test", base_url=None)

    assert isinstance(openai, OpenAICompatibleProvider)
    assert isinstance(nvidia, NvidiaProvider)
    assert isinstance(deepseek, DeepSeekProvider)


def test_create_provider_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unknown provider type"):
        _create_provider(provider_type="missing", api_key=None, base_url=None)


def test_create_provider_bedrock_passes_aws_credentials() -> None:
    with patch("kernel.llm_provider.bedrock.AsyncAnthropicBedrock") as cls:
        provider = _create_provider(
            provider_type="bedrock",
            api_key="aws-key",
            base_url=None,
            aws_secret_key="aws-secret",
            aws_region="us-east-1",
        )

    cls.assert_called_once_with(
        aws_access_key="aws-key",
        aws_secret_key="aws-secret",
        aws_region="us-east-1",
    )
    assert provider._client is cls.return_value


async def test_bedrock_context_window_strips_region_prefix() -> None:
    with patch("kernel.llm_provider.bedrock.AsyncAnthropicBedrock"):
        provider = _create_provider(
            provider_type="bedrock",
            api_key=None,
            base_url=None,
            aws_secret_key=None,
            aws_region=None,
        )

    assert await provider.context_window("us.claude-sonnet-4-6") == 200_000
