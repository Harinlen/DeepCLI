"""Tests for MiniMax provider — context window detection via OpenRouter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.config.schema import ProviderRuntimeConfig
from daemon.providers.minimax import MiniMaxProvider, _query_openrouter_context_window


@pytest.fixture
def minimax_config() -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        type="minimax",
        base_url="https://api.minimax.io/v1",
        model="MiniMax-M2.7",
        api_key="test-key",
    )


class TestMiniMaxProvider:
    """Tests for MiniMaxProvider class."""

    def test_name(self, minimax_config: ProviderRuntimeConfig) -> None:
        provider = MiniMaxProvider(minimax_config, provider_name="minimax")
        assert provider.name == "minimax"

    def test_inherits_openai_base(self, minimax_config: ProviderRuntimeConfig) -> None:
        """MiniMaxProvider inherits message translation from OpenAIBaseProvider."""
        from daemon.providers.openai_base import OpenAIBaseProvider

        provider = MiniMaxProvider(minimax_config, provider_name="minimax")
        assert isinstance(provider, OpenAIBaseProvider)


class TestQueryOpenRouterContextWindow:
    """Tests for _query_openrouter_context_window."""

    @pytest.mark.asyncio
    async def test_found_model(self) -> None:
        """Returns context_length when model is found on OpenRouter."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "minimax/minimax-m2.7", "context_length": 204800},
                {"id": "minimax/minimax-m2.5", "context_length": 196608},
            ]
        }

        with patch("daemon.providers.minimax.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _query_openrouter_context_window("MiniMax-M2.7")
            assert result == 204800

    @pytest.mark.asyncio
    async def test_model_not_found(self) -> None:
        """Returns None when model is not on OpenRouter."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "openai/gpt-4o", "context_length": 128000},
            ]
        }

        with patch("daemon.providers.minimax.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _query_openrouter_context_window("Unknown-Model")
            assert result is None

    @pytest.mark.asyncio
    async def test_network_error(self) -> None:
        """Returns None on network failure."""
        with patch("daemon.providers.minimax.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _query_openrouter_context_window("MiniMax-M2.7")
            assert result is None

    @pytest.mark.asyncio
    async def test_case_insensitive_lookup(self) -> None:
        """Model name is lowercased for OpenRouter lookup."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "minimax/minimax-m2.7", "context_length": 204800},
            ]
        }

        with patch("daemon.providers.minimax.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # Mixed case input should still match
            result = await _query_openrouter_context_window("MiniMax-M2.7")
            assert result == 204800
