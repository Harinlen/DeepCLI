"""Tests for LLMManager provider CRUD -- add_provider, remove_provider,
set_current_model, list_providers, refresh_models.

These methods mutate in-memory state and persist via _cfg_section.update().
We test them by bypassing startup() and mocking _cfg_section + _provider_manager.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from kernel.agents.mustang.llm import LLMManager
from kernel.agents.mustang.llm.config import (
    CurrentUsedConfig,
    LLMConfig,
    ModelRef,
    ModelSpec,
    ProviderConfig,
)
from kernel.agents.mustang.llm.errors import ModelNotFoundError
from kernel.agents.mustang.llm.types import LLMChunk, ModelInfo
from kernel.agents.mustang.llm_provider.base import Provider
from kernel.core.protocol.interfaces.contracts.add_model_params import AddModelParams
from kernel.core.protocol.interfaces.contracts.add_provider_params import AddProviderParams
from kernel.core.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.core.protocol.interfaces.contracts.list_providers_params import ListProvidersParams
from kernel.core.protocol.interfaces.contracts.refresh_models_params import RefreshModelsParams
from kernel.core.protocol.interfaces.contracts.remove_provider_params import RemoveProviderParams
from kernel.core.protocol.interfaces.contracts.set_current_model_params import SetCurrentModelParams
from kernel.core.protocol.interfaces.contracts.update_model_params import UpdateModelParams


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeProvider(Provider):
    def __init__(self, discovered: list[str] | None = None) -> None:
        self._discovered = discovered or []

    def stream(self, **kwargs) -> AsyncGenerator[LLMChunk, None]:
        raise NotImplementedError

    async def models(self) -> list[ModelInfo]:
        return []

    async def discover_models(self) -> list[str]:
        return self._discovered


def _make_manager(
    providers: dict[str, ProviderConfig] | None = None,
    aliases: dict[str, ModelRef] | None = None,
    default: ModelRef | None = None,
    fake_provider: FakeProvider | None = None,
) -> LLMManager:
    """Build LLMManager with mocked infrastructure."""
    if providers is None:
        providers = {
            "anthropic": ProviderConfig(
                type="anthropic",
                api_key="sk-a",
                models=[
                    ModelSpec(id="claude-opus-4-6"),
                    ModelSpec(id="claude-sonnet-4-6"),
                ],
            ),
        }
    if default is None:
        default = ModelRef(provider="anthropic", model="claude-opus-4-6")

    mgr = LLMManager.__new__(LLMManager)
    mgr._module_table = MagicMock()
    mgr._aliases = aliases or {}
    mgr._providers = dict(providers)
    mgr._current_used = CurrentUsedConfig(default=default)

    # Mock provider manager -- always returns a FakeProvider
    pm = MagicMock()
    pm.get_provider.return_value = fake_provider or FakeProvider()
    mgr._provider_manager = pm

    # Mock config section for _persist()
    section = MagicMock()
    section.get.return_value = LLMConfig(
        providers=dict(providers),
        current_used=CurrentUsedConfig(default=default),
    )
    section.update = AsyncMock()
    mgr._cfg_section = section

    return mgr


def _ctx() -> HandlerContext:
    return HandlerContext(conn=MagicMock(), sender=MagicMock(), request_id=1)


# ---------------------------------------------------------------------------
# list_providers
# ---------------------------------------------------------------------------


class TestListProviders:
    async def test_lists_all_providers(self) -> None:
        mgr = _make_manager()
        result = await mgr.list_providers(_ctx(), ListProvidersParams())
        assert len(result.providers) == 1
        assert result.providers[0].name == "anthropic"
        assert result.providers[0].setting_fields == ["api_key", "base_url"]
        assert len(result.providers[0].models) == 2
        assert result.providers[0].context_windows == {
            "claude-opus-4-6": 128_000,
            "claude-sonnet-4-6": 128_000,
        }
        assert result.default_context_window == 128_000

    async def test_current_used_field(self) -> None:
        mgr = _make_manager()
        result = await mgr.list_providers(_ctx(), ListProvidersParams())
        assert result.current_used == {"default": ["anthropic", "claude-opus-4-6"]}

    async def test_model_overrides(self) -> None:
        mgr = _make_manager(
            providers={
                "anthropic": ProviderConfig(
                    type="anthropic",
                    api_key="sk-a",
                    models=[
                        ModelSpec(
                            id="claude-opus-4-6",
                            display_name="Opus",
                            context_window=200_000,
                        ),
                    ],
                ),
            },
            default=ModelRef(provider="anthropic", model="claude-opus-4-6"),
        )
        result = await mgr.list_providers(_ctx(), ListProvidersParams())
        assert result.providers[0].display_names == {"claude-opus-4-6": "Opus"}
        assert result.providers[0].context_windows == {"claude-opus-4-6": 200_000}


# ---------------------------------------------------------------------------
# add_provider
# ---------------------------------------------------------------------------


class TestAddProvider:
    async def test_add_with_explicit_models(self) -> None:
        mgr = _make_manager()
        params = AddProviderParams(
            name="bedrock",
            provider_type="bedrock",
            api_key="AKIA...",
            aws_secret_key="secret",
            aws_region="us-east-1",
            models=["us.anthropic.claude-sonnet-4-6", "us.anthropic.claude-haiku-4-5"],
        )
        result = await mgr.add_provider(_ctx(), params)
        assert result.name == "bedrock"
        assert len(result.models) == 2
        assert "bedrock" in mgr._providers
        mgr._cfg_section.update.assert_awaited_once()

    async def test_add_with_auto_discovery(self) -> None:
        discovered = ["model-a", "model-b", "model-c"]
        mgr = _make_manager(fake_provider=FakeProvider(discovered=discovered))
        params = AddProviderParams(
            name="local",
            provider_type="openai_compatible",
            base_url="http://localhost:11434/v1",
        )
        result = await mgr.add_provider(_ctx(), params)
        assert result.models == discovered

    async def test_add_duplicate_raises(self) -> None:
        mgr = _make_manager()
        params = AddProviderParams(
            name="anthropic",
            provider_type="anthropic",
            api_key="sk-x",
        )
        with pytest.raises(ValueError, match="already exists"):
            await mgr.add_provider(_ctx(), params)

    async def test_add_no_discovery_no_models_raises(self) -> None:
        mgr = _make_manager(fake_provider=FakeProvider(discovered=[]))
        params = AddProviderParams(
            name="empty",
            provider_type="openai_compatible",
            base_url="http://localhost:1234/v1",
        )
        with pytest.raises(ValueError, match="no models"):
            await mgr.add_provider(_ctx(), params)


# ---------------------------------------------------------------------------
# remove_provider
# ---------------------------------------------------------------------------


class TestRemoveProvider:
    async def test_remove_existing(self) -> None:
        providers = {
            "anthropic": ProviderConfig(
                type="anthropic", api_key="sk-a",
                models=[ModelSpec(id="claude-opus-4-6")],
            ),
            "bedrock": ProviderConfig(
                type="bedrock", api_key="AKIA",
                aws_secret_key="s", aws_region="us-east-1",
                models=[ModelSpec(id="us.anthropic.claude-sonnet-4-6")],
            ),
        }
        mgr = _make_manager(providers=providers)
        await mgr.remove_provider(_ctx(), RemoveProviderParams(name="bedrock"))
        assert "bedrock" not in mgr._providers
        mgr._cfg_section.update.assert_awaited_once()

    async def test_remove_nonexistent_raises(self) -> None:
        mgr = _make_manager()
        with pytest.raises(ValueError, match="does not exist"):
            await mgr.remove_provider(_ctx(), RemoveProviderParams(name="ghost"))

    async def test_remove_last_raises(self) -> None:
        mgr = _make_manager()
        with pytest.raises(ValueError, match="last provider"):
            await mgr.remove_provider(_ctx(), RemoveProviderParams(name="anthropic"))

    async def test_remove_default_rebinds(self) -> None:
        """Removing the default's provider re-binds to the first remaining."""
        providers = {
            "anthropic": ProviderConfig(
                type="anthropic", api_key="sk-a",
                models=[ModelSpec(id="claude-opus-4-6")],
            ),
            "bedrock": ProviderConfig(
                type="bedrock", api_key="AKIA",
                aws_secret_key="s", aws_region="us-east-1",
                models=[ModelSpec(id="us.anthropic.claude-sonnet-4-6")],
            ),
        }
        mgr = _make_manager(
            providers=providers,
            default=ModelRef(provider="anthropic", model="claude-opus-4-6"),
        )
        await mgr.remove_provider(_ctx(), RemoveProviderParams(name="anthropic"))
        assert mgr._current_used.default.provider == "bedrock"


# ---------------------------------------------------------------------------
# refresh_models
# ---------------------------------------------------------------------------


class TestRefreshModels:
    async def test_refresh_updates_models(self) -> None:
        new_models = ["new-model-a", "new-model-b"]
        mgr = _make_manager(fake_provider=FakeProvider(discovered=new_models))
        result = await mgr.refresh_models(_ctx(), RefreshModelsParams(name="anthropic"))
        assert result.models == new_models
        assert len(mgr._providers["anthropic"].models) == 2

    async def test_refresh_nonexistent_raises(self) -> None:
        mgr = _make_manager()
        with pytest.raises(ValueError, match="does not exist"):
            await mgr.refresh_models(_ctx(), RefreshModelsParams(name="ghost"))

    async def test_refresh_empty_raises(self) -> None:
        mgr = _make_manager(fake_provider=FakeProvider(discovered=[]))
        with pytest.raises(ValueError, match="no models"):
            await mgr.refresh_models(_ctx(), RefreshModelsParams(name="anthropic"))


# ---------------------------------------------------------------------------
# set_current_model
# ---------------------------------------------------------------------------


class TestSetCurrentModel:
    async def test_set_valid_ref(self) -> None:
        mgr = _make_manager()
        ref = ModelRef(provider="anthropic", model="claude-sonnet-4-6")
        result = await mgr.set_current_model(_ctx(), SetCurrentModelParams(model=ref))
        assert result.role == "default"
        assert result.model == ["anthropic", "claude-sonnet-4-6"]
        assert mgr._current_used.default == ref

    async def test_set_valid_non_default_role(self) -> None:
        mgr = _make_manager()
        ref = ModelRef(provider="anthropic", model="claude-sonnet-4-6")
        result = await mgr.set_current_model(
            _ctx(),
            SetCurrentModelParams(role="compact", model=ref),
        )
        assert result.role == "compact"
        assert result.model == ["anthropic", "claude-sonnet-4-6"]
        assert mgr._current_used.compact == ref

    async def test_unknown_role_raises(self) -> None:
        mgr = _make_manager()
        ref = ModelRef(provider="anthropic", model="claude-sonnet-4-6")
        with pytest.raises(ValueError, match="Unknown model role"):
            await mgr.set_current_model(
                _ctx(),
                SetCurrentModelParams(role="ghost", model=ref),
            )

    async def test_unknown_raises(self) -> None:
        mgr = _make_manager()
        ref = ModelRef(provider="ghost", model="x")
        with pytest.raises(ModelNotFoundError):
            await mgr.set_current_model(_ctx(), SetCurrentModelParams(model=ref))

    async def test_persists(self) -> None:
        mgr = _make_manager()
        ref = ModelRef(provider="anthropic", model="claude-sonnet-4-6")
        await mgr.set_current_model(_ctx(), SetCurrentModelParams(model=ref))
        mgr._cfg_section.update.assert_awaited_once()


# ---------------------------------------------------------------------------
# add_model
# ---------------------------------------------------------------------------


class TestAddModel:
    async def test_adds_model_to_existing_provider(self) -> None:
        mgr = _make_manager()
        result = await mgr.add_model(
            _ctx(),
            AddModelParams(
                provider_name="anthropic",
                model_id="claude-haiku-4-5",
                display_name="Haiku",
                context_window=200_000,
                roles=["compact"],
            ),
        )
        assert result.model == ["anthropic", "claude-haiku-4-5"]
        assert result.display_name == "Haiku"
        assert result.roles == ["compact"]
        assert mgr._providers["anthropic"].models[-1].id == "claude-haiku-4-5"
        assert mgr._current_used.compact == ModelRef(
            provider="anthropic",
            model="claude-haiku-4-5",
        )
        mgr._cfg_section.update.assert_awaited_once()

    async def test_creates_new_provider_with_one_model(self) -> None:
        mgr = _make_manager()
        result = await mgr.add_model(
            _ctx(),
            AddModelParams(
                provider_name="nvidia",
                provider_type="nvidia",
                api_key="nv-key",
                model_id="minimaxai/minimax-m2.7",
            ),
        )
        assert result.model == ["nvidia", "minimaxai/minimax-m2.7"]
        assert result.provider_type == "nvidia"
        assert result.effective_base_url == "https://integrate.api.nvidia.com/v1"
        assert mgr._providers["nvidia"].api_key == "nv-key"
        assert mgr._providers["nvidia"].models[0].id == "minimaxai/minimax-m2.7"

    async def test_duplicate_model_raises(self) -> None:
        mgr = _make_manager()
        with pytest.raises(ValueError, match="already exists"):
            await mgr.add_model(
                _ctx(),
                AddModelParams(
                    provider_name="anthropic",
                    model_id="claude-opus-4-6",
                ),
            )


# ---------------------------------------------------------------------------
# update_model
# ---------------------------------------------------------------------------


class TestUpdateModel:
    async def test_updates_model_settings_and_roles(self) -> None:
        mgr = _make_manager()
        ref = ModelRef(provider="anthropic", model="claude-sonnet-4-6")
        result = await mgr.update_model(
            _ctx(),
            UpdateModelParams(
                model=ref,
                display_name="Sonnet",
                context_window=200_000,
                roles=["default", "compact"],
            ),
        )
        assert result.model == ["anthropic", "claude-sonnet-4-6"]
        assert result.provider_type == "anthropic"
        assert result.display_name == "Sonnet"
        assert result.context_window == 200_000
        assert result.roles == ["compact", "default"]
        assert mgr._current_used.default == ref
        assert mgr._current_used.compact == ref
        assert mgr._providers["anthropic"].models[1].display_name == "Sonnet"
        assert mgr._providers["anthropic"].models[1].context_window == 200_000
        mgr._cfg_section.update.assert_awaited_once()

    async def test_renames_provider_and_model_id(self) -> None:
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        mgr = _make_manager(aliases={"opus": ref}, default=ref)
        result = await mgr.update_model(
            _ctx(),
            UpdateModelParams(
                model=ref,
                provider_name="anthropic-prod",
                provider_type="openai_compatible",
                base_url="https://example.test/v1",
                model_id="opus-renamed",
                roles=["default"],
            ),
        )
        new_ref = ModelRef(provider="anthropic-prod", model="opus-renamed")
        assert result.model == ["anthropic-prod", "opus-renamed"]
        assert result.provider_type == "openai_compatible"
        assert result.setting_fields == ["api_key", "base_url"]
        assert result.base_url == "https://example.test/v1"
        assert "anthropic" not in mgr._providers
        assert mgr._providers["anthropic-prod"].models[0].id == "opus-renamed"
        assert mgr._current_used.default == new_ref
        assert mgr._aliases["opus"] == new_ref

    async def test_removes_existing_role_when_not_requested(self) -> None:
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        mgr = _make_manager(default=ref)
        result = await mgr.update_model(
            _ctx(),
            UpdateModelParams(model=ref, roles=[]),
        )
        assert result.roles == []
        assert mgr._current_used.default is None

    async def test_unknown_role_raises(self) -> None:
        mgr = _make_manager()
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        with pytest.raises(ValueError, match="Unknown model role"):
            await mgr.update_model(
                _ctx(),
                UpdateModelParams(model=ref, roles=["ghost"]),
            )
