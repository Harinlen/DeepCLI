"""LLMManager -- provider configuration management and stream routing.

Implements two Protocols consumed by the rest of the kernel:

- ``LLMProvider`` (consumed by Orchestrator) -- ``stream`` / ``models`` /
  ``context_window`` / ``model_for``.
- ``ModelHandler`` (consumed by protocol layer) -- runtime CRUD for
  providers: ``list_providers`` / ``add_provider`` / ``remove_provider`` /
  ``refresh_models`` / ``set_default_model``.

Reads user-defined provider configs, resolves aliases, and routes
``stream()`` calls to the correct ``Provider`` via ``LLMProviderManager``.

Runtime mutation (``add_provider``, ``remove_provider``, ``set_default_model``)
updates both the in-memory registry and the on-disk config atomically via
``MutableSection.update()``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from kernel.llm.config import (
    CurrentUsedConfig,
    LLMConfig,
    ModelRef,
    ModelSpec,
    ProviderConfig,
)
from kernel.llm.errors import ModelNotFoundError
from kernel.llm.types import (
    LLMChunk,
    Message,
    ModelInfo,
    PromptSection,
    ToolSchema,
)
from kernel.protocol.interfaces.contracts.add_provider_params import AddProviderParams
from kernel.protocol.interfaces.contracts.add_provider_result import AddProviderResult
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.list_profiles_params import ListProfilesParams
from kernel.protocol.interfaces.contracts.list_profiles_result import (
    ListProfilesResult,
    ProfileInfo,
)
from kernel.protocol.interfaces.contracts.list_providers_params import ListProvidersParams
from kernel.protocol.interfaces.contracts.list_providers_result import (
    ListProvidersResult,
    ProviderInfo,
)
from kernel.protocol.interfaces.contracts.refresh_models_params import RefreshModelsParams
from kernel.protocol.interfaces.contracts.refresh_models_result import RefreshModelsResult
from kernel.protocol.interfaces.contracts.remove_provider_params import RemoveProviderParams
from kernel.protocol.interfaces.contracts.remove_provider_result import RemoveProviderResult
from kernel.protocol.interfaces.contracts.set_default_model_params import SetDefaultModelParams
from kernel.protocol.interfaces.contracts.set_default_model_result import SetDefaultModelResult
from kernel.subsystem import Subsystem

if TYPE_CHECKING:
    from kernel.config.section import MutableSection
    from kernel.llm_provider import LLMProviderManager
    from kernel.llm_provider.base import Provider

logger = logging.getLogger(__name__)

_CONFIG_FILE = "kernel"
_CONFIG_SECTION = "llm"


class LLMManager(Subsystem):
    """Provider configuration manager.

    Implements both ``LLMProvider`` Protocol (for Orchestrator) and
    ``ModelHandler`` Protocol (for the ACP protocol layer).

    Startup
    -------
    1. Binds ``LLMConfig`` section via ``ConfigManager.bind_section``
       (owner, not reader -- required for runtime mutations).
    2. Merges built-in aliases with user-defined aliases.
    3. Calls ``LLMProviderManager.get_provider()`` for each provider entry
       to pre-warm the provider cache.
    4. Validates all ``current_used`` refs resolve to known providers/models.
    """

    async def startup(self) -> None:
        # Bind (not just get) -- we need write access for runtime CRUD.
        self._cfg_section: MutableSection[LLMConfig] = self._module_table.config.bind_section(
            file=_CONFIG_FILE,
            section=_CONFIG_SECTION,
            schema=LLMConfig,
        )
        config: LLMConfig = self._cfg_section.get()

        self._aliases: dict[str, ModelRef] = dict(config.model_aliases)
        self._providers: dict[str, ProviderConfig] = dict(config.providers)
        self._current_used: CurrentUsedConfig = config.current_used

        # Pre-warm provider cache
        from kernel.llm_provider import LLMProviderManager

        self._provider_manager: LLMProviderManager = self._module_table.get(LLMProviderManager)
        for name, pcfg in list(self._providers.items()):
            try:
                self._provider_manager.get_provider(
                    provider_type=pcfg.type,
                    api_key=pcfg.api_key,
                    base_url=pcfg.base_url,
                    aws_secret_key=pcfg.aws_secret_key,
                    aws_region=pcfg.aws_region,
                )
                model_count = len(pcfg.models) if pcfg.models else 0
                logger.info(
                    "LLMManager: registered provider '%s' (%d models)",
                    name,
                    model_count,
                )
            except Exception:
                logger.exception(
                    "LLMManager: failed to create provider '%s' -- skipping",
                    name,
                )
                del self._providers[name]

        # Fail-fast: every role in current_used must resolve.
        for role_name, model_ref in self._current_used.model_dump().items():
            if model_ref is None:
                continue
            ref = ModelRef.model_validate(model_ref)
            self._validate_ref(ref)

        logger.info(
            "LLMManager: %d provider(s) loaded, current_used=%s",
            len(self._providers),
            self._current_used.model_dump(exclude_none=True),
        )

    async def shutdown(self) -> None:
        self._providers.clear()

    # ------------------------------------------------------------------
    # LLMProvider Protocol (consumed by Orchestrator)
    # ------------------------------------------------------------------

    async def stream(
        self,
        *,
        system: list[PromptSection],
        messages: list[Message],
        tool_schemas: list[ToolSchema],
        model: ModelRef,
        temperature: float | None,
        thinking: bool = False,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        """Route a streaming request to the correct provider.

        ``max_tokens`` overrides the model spec value when non-None.
        """
        spec, provider = self._resolve(model)
        return provider.stream(
            system=system,
            messages=messages,
            tool_schemas=tool_schemas,
            model_id=spec.id,
            temperature=temperature,
            thinking=thinking and spec.thinking,
            max_tokens=max_tokens if max_tokens is not None else spec.max_tokens,
            prompt_caching=spec.prompt_caching,
        )

    async def models(self) -> list[ModelInfo]:
        """Return metadata for all registered models across all providers."""
        result: list[ModelInfo] = []
        for prov_name, pcfg in self._providers.items():
            provider = self._get_provider_instance(pcfg)
            for spec in pcfg.models or []:
                cw = await provider.context_window(spec.id)
                result.append(
                    ModelInfo(
                        id=f"{prov_name}/{spec.id}",
                        provider_type=pcfg.type,
                        model_id=spec.id,
                        context_window=cw,
                    )
                )
        return result

    async def context_window(self, model: ModelRef) -> int | None:
        """Return context window size for a model ref."""
        try:
            spec, provider = self._resolve(model)
        except ModelNotFoundError:
            return None
        return await provider.context_window(spec.id)

    def model_for(self, role: str) -> ModelRef:
        """Return the ModelRef assigned to ``role``.

        ``role="default"`` is the only role guaranteed to exist.

        Raises:
            KeyError: if ``role`` is not a field of ``CurrentUsedConfig``
                or is explicitly set to ``None``.
        """
        value = getattr(self._current_used, role, None)
        if value is None:
            raise KeyError(f"No model assigned for role: {role!r}")
        return value

    def model_for_or_default(self, role: str) -> ModelRef:
        """Return the ModelRef for ``role`` with graceful fallback.

        Unlike :meth:`model_for`, this never raises: if *role* is
        unconfigured it returns the ``default`` ref.  Use this from
        callers (Compactor, WebFetch secondary-model) that can happily
        run on the main model when the user has not configured a
        dedicated cheaper/faster model for their role.
        """
        value = getattr(self._current_used, role, None)
        if value is None:
            return self._current_used.default
        return value

    # ------------------------------------------------------------------
    # ModelHandler Protocol (consumed by ACP protocol layer)
    # ------------------------------------------------------------------

    async def list_profiles(
        self, ctx: HandlerContext, params: ListProfilesParams
    ) -> ListProfilesResult:
        """Return all provider×model combinations as flat profile entries.

        One ``ProfileInfo`` is emitted per (provider, model_id) pair.
        The ``is_default`` flag is set on the entry that matches the
        current ``current_used.default`` ref.
        """
        default_ref = self._current_used.default
        profiles: list[ProfileInfo] = []
        for provider_name, pcfg in self._providers.items():
            for spec in (pcfg.models or []):
                is_default = (
                    default_ref.provider == provider_name
                    and default_ref.model == spec.id
                )
                profiles.append(
                    ProfileInfo(
                        name=f"{provider_name}/{spec.id}",
                        provider_type=pcfg.type,
                        model_id=spec.id,
                        is_default=is_default,
                    )
                )
        default_label = f"{default_ref.provider}/{default_ref.model}"
        return ListProfilesResult(profiles=profiles, default_model=default_label)

    async def list_providers(
        self, ctx: HandlerContext, params: ListProvidersParams
    ) -> ListProvidersResult:
        """Return all registered providers and their models."""
        default_ref = self._current_used.default
        providers = []
        for name, pcfg in self._providers.items():
            model_ids = [s.id for s in (pcfg.models or [])]
            # Compute role assignments for this provider
            roles: dict[str, bool] = {}
            for role_name, ref in self._current_used.model_dump().items():
                if ref is None:
                    continue
                ref_obj = ModelRef.model_validate(ref)
                roles[role_name] = ref_obj.provider == name
            providers.append(
                ProviderInfo(
                    name=name,
                    provider_type=pcfg.type,
                    models=model_ids,
                    roles=roles,
                )
            )
        return ListProvidersResult(
            providers=providers,
            default_model=default_ref.to_list(),
        )

    async def add_provider(
        self, ctx: HandlerContext, params: AddProviderParams
    ) -> AddProviderResult:
        """Add a new provider and persist to config.

        For providers that support auto-discovery (anthropic, openai_compatible,
        nvidia), omitting ``models`` triggers discovery. For bedrock, ``models``
        is required.
        """
        if params.name in self._providers:
            raise ValueError(
                f"Provider '{params.name}' already exists. Use remove_provider first to replace it."
            )

        # Create provider instance (validates credentials).
        provider = self._provider_manager.get_provider(
            provider_type=params.provider_type,
            api_key=params.api_key,
            base_url=params.base_url,
            aws_secret_key=params.aws_secret_key,
            aws_region=params.aws_region,
        )

        # Resolve model list.
        if params.models is not None:
            model_ids = params.models
        else:
            # Auto-discover.
            model_ids = await provider.discover_models()
            if not model_ids:
                raise ValueError(
                    f"Provider type '{params.provider_type}' returned no models "
                    "from auto-discovery. Please specify models explicitly."
                )

        pcfg = ProviderConfig(
            type=params.provider_type,
            api_key=params.api_key,
            base_url=params.base_url,
            aws_secret_key=params.aws_secret_key,
            aws_region=params.aws_region,
            models=[ModelSpec(id=m) for m in model_ids],
        )

        self._providers[params.name] = pcfg
        await self._persist()

        logger.info("LLMManager: added provider '%s' with %d models", params.name, len(model_ids))
        return AddProviderResult(name=params.name, models=model_ids)

    async def remove_provider(
        self, ctx: HandlerContext, params: RemoveProviderParams
    ) -> RemoveProviderResult:
        """Remove a provider and persist to config."""
        if params.name not in self._providers:
            raise ValueError(f"Provider '{params.name}' does not exist.")
        if len(self._providers) == 1:
            raise ValueError("Cannot remove the last provider.")

        # Check if current_used references this provider.
        if self._current_used.default.provider == params.name:
            # Re-bind default to first remaining provider's first model.
            for other_name, other_pcfg in self._providers.items():
                if other_name != params.name and other_pcfg.models:
                    fallback = ModelRef(provider=other_name, model=other_pcfg.models[0].id)
                    self._current_used = self._current_used.model_copy(update={"default": fallback})
                    logger.info(
                        "LLMManager: default re-bound to [%s, %s] after removing '%s'",
                        other_name,
                        other_pcfg.models[0].id,
                        params.name,
                    )
                    break

        del self._providers[params.name]
        await self._persist()

        logger.info("LLMManager: removed provider '%s'", params.name)
        return RemoveProviderResult()

    async def refresh_models(
        self, ctx: HandlerContext, params: RefreshModelsParams
    ) -> RefreshModelsResult:
        """Re-discover models for a provider and persist."""
        if params.name not in self._providers:
            raise ValueError(f"Provider '{params.name}' does not exist.")

        pcfg = self._providers[params.name]
        provider = self._get_provider_instance(pcfg)
        model_ids = await provider.discover_models()
        if not model_ids:
            raise ValueError(
                f"Provider '{params.name}' returned no models from discovery. "
                "The existing model list is unchanged."
            )

        pcfg_updated = pcfg.model_copy(update={"models": [ModelSpec(id=m) for m in model_ids]})
        self._providers[params.name] = pcfg_updated
        await self._persist()

        logger.info(
            "LLMManager: refreshed provider '%s', %d models found",
            params.name,
            len(model_ids),
        )
        return RefreshModelsResult(models=model_ids)

    async def set_default_model(
        self, ctx: HandlerContext, params: SetDefaultModelParams
    ) -> SetDefaultModelResult:
        """Set the kernel-wide default model and persist."""
        ref = params.model
        self._validate_ref(ref)

        self._current_used = self._current_used.model_copy(update={"default": ref})
        await self._persist()

        logger.info("LLMManager: default set to [%s, %s]", ref.provider, ref.model)
        return SetDefaultModelResult(default_model=ref.to_list())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve(self, model_ref: ModelRef | str) -> tuple[ModelSpec, Provider]:
        """Resolve a ModelRef (or alias string) to (ModelSpec, Provider).

        Raises ``ModelNotFoundError`` for unknown refs.
        """
        if isinstance(model_ref, str):
            # Try alias lookup
            alias_ref = self._aliases.get(model_ref)
            if alias_ref is None:
                raise ModelNotFoundError(
                    model_ref,
                    known=self._all_model_keys(),
                )
            model_ref = alias_ref

        pcfg = self._providers.get(model_ref.provider)
        if pcfg is None:
            raise ModelNotFoundError(
                f"{model_ref.provider}/{model_ref.model}",
                known=self._all_model_keys(),
            )

        spec = self._find_model_spec(pcfg, model_ref.model)
        if spec is None:
            raise ModelNotFoundError(
                f"{model_ref.provider}/{model_ref.model}",
                known=self._all_model_keys(),
            )

        provider = self._get_provider_instance(pcfg)
        return spec, provider

    def _get_provider_instance(self, pcfg: ProviderConfig) -> Provider:
        """Get the cached Provider instance for a ProviderConfig."""
        return self._provider_manager.get_provider(
            provider_type=pcfg.type,
            api_key=pcfg.api_key,
            base_url=pcfg.base_url,
            aws_secret_key=pcfg.aws_secret_key,
            aws_region=pcfg.aws_region,
        )

    def _find_model_spec(self, pcfg: ProviderConfig, model_id: str) -> ModelSpec | None:
        """Find a ModelSpec by model_id within a provider's model list."""
        if pcfg.models is None:
            return None
        for spec in pcfg.models:
            if spec.id == model_id:
                return spec
        return None

    def _validate_ref(self, ref: ModelRef) -> None:
        """Raise ModelNotFoundError if the ref doesn't resolve."""
        pcfg = self._providers.get(ref.provider)
        if pcfg is None:
            raise ModelNotFoundError(
                f"{ref.provider}/{ref.model}",
                known=self._all_model_keys(),
            )
        if self._find_model_spec(pcfg, ref.model) is None:
            raise ModelNotFoundError(
                f"{ref.provider}/{ref.model}",
                known=self._all_model_keys(),
            )

    def _all_model_keys(self) -> list[str]:
        """Return sorted list of all ``provider/model_id`` strings."""
        keys: list[str] = []
        for prov_name, pcfg in self._providers.items():
            for spec in pcfg.models or []:
                keys.append(f"{prov_name}/{spec.id}")
        return sorted(keys)

    async def _persist(self) -> None:
        """Rebuild LLMConfig from current in-memory state and write to disk."""
        new_config = LLMConfig(
            providers=dict(self._providers),
            current_used=self._current_used,
            model_aliases=dict(self._aliases),
        )
        await self._cfg_section.update(new_config)
