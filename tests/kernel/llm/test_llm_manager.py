"""Tests for LLMManager -- alias resolution and stream routing.

Uses a FakeProvider and bypasses startup() to exercise the routing
logic without ConfigManager or real API calls.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest

from kernel.llm import LLMManager
from kernel.llm.config import (
    CurrentUsedConfig,
    ModelRef,
    ModelSpec,
    ProviderConfig,
)
from kernel.llm.errors import ModelNotFoundError
from kernel.llm.types import (
    LLMChunk,
    ModelInfo,
    TextChunk,
    UsageChunk,
)
from kernel.llm_provider.base import Provider


# ---------------------------------------------------------------------------
# FakeProvider -- records calls, yields fixed chunks
# ---------------------------------------------------------------------------


class FakeProvider(Provider):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def stream(
        self,
        *,
        system,
        messages,
        tool_schemas,
        model_id,
        temperature,
        thinking,
        max_tokens,
        prompt_caching,
    ) -> AsyncGenerator[LLMChunk, None]:
        self.calls.append(
            {
                "model_id": model_id,
                "temperature": temperature,
                "thinking": thinking,
                "max_tokens": max_tokens,
                "prompt_caching": prompt_caching,
            }
        )
        return self._gen()

    async def _gen(self) -> AsyncGenerator[LLMChunk, None]:
        yield TextChunk(content="hi")
        yield UsageChunk(input_tokens=5, output_tokens=3)

    async def models(self) -> list[ModelInfo]:
        return []


# ---------------------------------------------------------------------------
# Helper -- build LLMManager with pre-populated internal state
# ---------------------------------------------------------------------------


def _make_manager(
    *,
    providers: dict[str, tuple[ProviderConfig, FakeProvider]],
    aliases: dict[str, ModelRef] | None = None,
    default: ModelRef | None = None,
) -> LLMManager:
    """Build LLMManager bypassing startup()."""

    class FakeProviderManager:
        def __init__(self, mapping: dict[tuple, FakeProvider]) -> None:
            self._mapping = mapping

        def get_provider(
            self, *, provider_type, api_key, base_url, aws_secret_key=None, aws_region=None
        ) -> FakeProvider:
            key = (provider_type, api_key, base_url)
            return self._mapping[key]

    provider_map: dict[tuple, FakeProvider] = {}
    provider_configs: dict[str, ProviderConfig] = {}

    for name, (pcfg, prov) in providers.items():
        provider_configs[name] = pcfg
        key = (pcfg.type, pcfg.api_key, pcfg.base_url)
        provider_map[key] = prov

    if default is None and providers:
        first_name = next(iter(providers))
        first_pcfg = providers[first_name][0]
        first_model = first_pcfg.models[0].id if first_pcfg.models else "unknown"
        default = ModelRef(provider=first_name, model=first_model)

    mgr = LLMManager.__new__(LLMManager)
    mgr._module_table = MagicMock()
    mgr._aliases = aliases or {}
    mgr._providers = provider_configs
    mgr._current_used = CurrentUsedConfig(
        default=default or ModelRef(provider="test", model="test"),
    )
    mgr._provider_manager = FakeProviderManager(provider_map)
    return mgr


def _pcfg(
    *model_ids: str,
    api_key: str = "sk-x",
    thinking: bool = False,
    prompt_caching: bool = True,
    max_tokens: int = 8192,
) -> ProviderConfig:
    """Build a ProviderConfig with given model IDs."""
    return ProviderConfig(
        type="anthropic",
        api_key=api_key,
        models=[
            ModelSpec(
                id=m,
                thinking=thinking,
                prompt_caching=prompt_caching,
                max_tokens=max_tokens,
            )
            for m in model_ids
        ],
    )


# ---------------------------------------------------------------------------
# _resolve tests
# ---------------------------------------------------------------------------


class TestResolve:
    def _manager(self) -> tuple[LLMManager, FakeProvider, FakeProvider]:
        pa = FakeProvider()
        pb = FakeProvider()
        mgr = _make_manager(
            providers={
                "anthropic": (_pcfg("claude-opus-4-6", api_key="sk-a"), pa),
                "bedrock": (_pcfg("claude-sonnet-4-6", api_key="sk-b"), pb),
            },
            aliases={
                "opus": ModelRef(provider="anthropic", model="claude-opus-4-6"),
                "sonnet": ModelRef(provider="bedrock", model="claude-sonnet-4-6"),
            },
            default=ModelRef(provider="anthropic", model="claude-opus-4-6"),
        )
        return mgr, pa, pb

    def test_resolve_by_model_ref(self):
        mgr, pa, _ = self._manager()
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        spec, provider = mgr._resolve(ref)
        assert spec.id == "claude-opus-4-6"
        assert provider is pa

    def test_resolve_by_alias(self):
        mgr, pa, _ = self._manager()
        spec, provider = mgr._resolve("opus")
        assert provider is pa

    def test_resolve_different_provider(self):
        mgr, _, pb = self._manager()
        spec, provider = mgr._resolve("sonnet")
        assert provider is pb

    def test_unknown_ref_raises(self):
        mgr, _, _ = self._manager()
        ref = ModelRef(provider="ghost", model="x")
        with pytest.raises(ModelNotFoundError):
            mgr._resolve(ref)

    def test_unknown_alias_raises(self):
        mgr, _, _ = self._manager()
        with pytest.raises(ModelNotFoundError):
            mgr._resolve("haiku")

    def test_error_includes_known_models(self):
        mgr, _, _ = self._manager()
        with pytest.raises(ModelNotFoundError) as exc_info:
            mgr._resolve("nope")
        assert "anthropic/claude-opus-4-6" in str(exc_info.value)


# ---------------------------------------------------------------------------
# stream routing tests
# ---------------------------------------------------------------------------


class TestStreamRouting:
    @pytest.mark.anyio
    async def test_stream_delegates_and_yields_chunks(self):
        p = FakeProvider()
        mgr = _make_manager(
            providers={"anthropic": (_pcfg("claude-opus-4-6"), p)},
        )
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        chunks = []
        async for chunk in await mgr.stream(
            system=[],
            messages=[],
            tool_schemas=[],
            model=ref,
            temperature=None,
        ):
            chunks.append(chunk)
        assert any(isinstance(c, TextChunk) for c in chunks)
        assert any(isinstance(c, UsageChunk) for c in chunks)

    @pytest.mark.anyio
    async def test_passes_resolved_model_id(self):
        p = FakeProvider()
        mgr = _make_manager(
            providers={"anthropic": (_pcfg("claude-opus-4-6"), p)},
            aliases={"opus": ModelRef(provider="anthropic", model="claude-opus-4-6")},
        )
        async for _ in await mgr.stream(
            system=[],
            messages=[],
            tool_schemas=[],
            model=ModelRef(provider="anthropic", model="claude-opus-4-6"),
            temperature=None,
        ):
            pass
        assert p.calls[0]["model_id"] == "claude-opus-4-6"

    @pytest.mark.anyio
    async def test_passes_max_tokens(self):
        p = FakeProvider()
        mgr = _make_manager(
            providers={"p": (_pcfg("m", max_tokens=2048), p)},
        )
        async for _ in await mgr.stream(
            system=[],
            messages=[],
            tool_schemas=[],
            model=ModelRef(provider="p", model="m"),
            temperature=None,
        ):
            pass
        assert p.calls[0]["max_tokens"] == 2048

    @pytest.mark.anyio
    async def test_passes_prompt_caching(self):
        p = FakeProvider()
        mgr = _make_manager(
            providers={"p": (_pcfg("m", prompt_caching=False), p)},
        )
        async for _ in await mgr.stream(
            system=[],
            messages=[],
            tool_schemas=[],
            model=ModelRef(provider="p", model="m"),
            temperature=None,
        ):
            pass
        assert p.calls[0]["prompt_caching"] is False

    @pytest.mark.anyio
    async def test_thinking_gated_by_model_spec(self):
        """thinking=True in stream() is AND-ed with spec.thinking."""
        p_no = FakeProvider()
        p_yes = FakeProvider()
        mgr = _make_manager(
            providers={
                "no": (_pcfg("m", thinking=False, api_key="sk-a"), p_no),
                "yes": (_pcfg("m", thinking=True, api_key="sk-b"), p_yes),
            },
        )
        async for _ in await mgr.stream(
            system=[], messages=[], tool_schemas=[],
            model=ModelRef(provider="no", model="m"),
            temperature=None, thinking=True,
        ):
            pass
        async for _ in await mgr.stream(
            system=[], messages=[], tool_schemas=[],
            model=ModelRef(provider="yes", model="m"),
            temperature=None, thinking=True,
        ):
            pass
        assert p_no.calls[0]["thinking"] is False
        assert p_yes.calls[0]["thinking"] is True

    @pytest.mark.anyio
    async def test_unknown_model_raises(self):
        mgr = _make_manager(providers={})
        with pytest.raises(ModelNotFoundError):
            await mgr.stream(
                system=[], messages=[], tool_schemas=[],
                model=ModelRef(provider="ghost", model="x"),
                temperature=None,
            )

    @pytest.mark.anyio
    async def test_two_models_share_provider_instance(self):
        """Two models under the same provider share one Provider instance."""
        shared = FakeProvider()
        mgr = _make_manager(
            providers={
                "anthropic": (
                    _pcfg("claude-opus-4-6", "claude-sonnet-4-6"),
                    shared,
                ),
            },
        )
        async for _ in await mgr.stream(
            system=[], messages=[], tool_schemas=[],
            model=ModelRef(provider="anthropic", model="claude-opus-4-6"),
            temperature=None,
        ):
            pass
        async for _ in await mgr.stream(
            system=[], messages=[], tool_schemas=[],
            model=ModelRef(provider="anthropic", model="claude-sonnet-4-6"),
            temperature=None,
        ):
            pass
        assert len(shared.calls) == 2
        assert shared.calls[0]["model_id"] == "claude-opus-4-6"
        assert shared.calls[1]["model_id"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# model_for
# ---------------------------------------------------------------------------


class TestModelFor:
    def test_default_role_returns_configured_ref(self):
        p = FakeProvider()
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        mgr = _make_manager(
            providers={"anthropic": (_pcfg("claude-opus-4-6"), p)},
            default=ref,
        )
        assert mgr.model_for("default") == ref

    def test_unknown_role_raises_keyerror(self):
        p = FakeProvider()
        mgr = _make_manager(
            providers={"anthropic": (_pcfg("claude-opus-4-6"), p)},
        )
        with pytest.raises(KeyError):
            mgr.model_for("compact")


# ---------------------------------------------------------------------------
# context_window
# ---------------------------------------------------------------------------


class TestContextWindow:
    @pytest.mark.anyio
    async def test_delegates_to_provider(self):
        p = FakeProvider()

        async def _cw(model_id: str) -> int | None:
            return 200_000

        p.context_window = _cw  # type: ignore[method-assign]

        mgr = _make_manager(
            providers={"anthropic": (_pcfg("claude-opus-4-6"), p)},
        )
        ref = ModelRef(provider="anthropic", model="claude-opus-4-6")
        assert await mgr.context_window(ref) == 200_000

    @pytest.mark.anyio
    async def test_unknown_model_returns_none(self):
        mgr = _make_manager(providers={})
        ref = ModelRef(provider="ghost", model="x")
        assert await mgr.context_window(ref) is None
