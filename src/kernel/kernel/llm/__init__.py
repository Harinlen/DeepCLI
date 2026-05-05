"""LLMManager -- provider configuration management and stream routing.

Implements two Protocols consumed by the rest of the kernel:

- ``LLMProvider`` (consumed by Orchestrator) -- ``stream`` / ``models`` /
  ``context_window`` / ``model_for``.
- ``ModelHandler`` (consumed by protocol layer) -- runtime CRUD for
  providers: ``list_providers`` / ``add_provider`` / ``remove_provider`` /
  ``refresh_models`` / ``set_current_model``.

Reads user-defined provider configs, resolves aliases, and routes
``stream()`` calls to the correct ``Provider`` via ``LLMProviderManager``.

Runtime mutation (``add_provider``, ``remove_provider``, ``set_current_model``)
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
from kernel.protocol.interfaces.contracts.add_model_params import AddModelParams
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
    ProviderTypeInfo,
)
from kernel.protocol.interfaces.contracts.refresh_models_params import RefreshModelsParams
from kernel.protocol.interfaces.contracts.refresh_models_result import RefreshModelsResult
from kernel.protocol.interfaces.contracts.remove_provider_params import RemoveProviderParams
from kernel.protocol.interfaces.contracts.remove_provider_result import RemoveProviderResult
from kernel.protocol.interfaces.contracts.set_current_model_params import SetCurrentModelParams
from kernel.protocol.interfaces.contracts.set_current_model_result import SetCurrentModelResult
from kernel.protocol.interfaces.contracts.update_model_params import UpdateModelParams
from kernel.protocol.interfaces.contracts.update_model_result import UpdateModelResult
from kernel.subsystem import Subsystem

if TYPE_CHECKING:
    from kernel.config.section import MutableSection
    from kernel.llm_provider import LLMProviderManager
    from kernel.llm_provider.base import Provider

logger = logging.getLogger(__name__)

_CONFIG_FILE = "kernel"
_CONFIG_SECTION = "llm"
_DEFAULT_CONTEXT_WINDOW = 128_000
_SUPPORTED_PROVIDER_TYPES = (
    "anthropic",
    "bedrock",
    "deepseek",
    "nvidia",
    "openai_compatible",
)


def normalize_optional_text(value: str | None, current: str | None) -> str | None:
    """Return stripped optional text, preserving current value when omitted."""
    if value is None:
        return current
    return value.strip() or None


def provider_setting_fields(provider_type: str) -> list[str]:
    """Return provider settings the UI should expose for this provider type."""
    if provider_type == "bedrock":
        return ["api_key", "aws_region", "aws_secret_key"]
    return ["api_key", "base_url"]


def provider_effective_base_url(provider_type: str, base_url: str | None) -> str | None:
    """Return the endpoint a provider will use after defaults are applied."""
    if base_url:
        return base_url
    match provider_type:
        case "openai_compatible":
            return "https://api.openai.com/v1"
        case "nvidia":
            return "https://integrate.api.nvidia.com/v1"
        case "deepseek":
            return "https://api.deepseek.com"
        case _:
            return None


def secret_display(value: str | None) -> str | None:
    """Return the raw secret for local user-facing configuration UIs."""
    if not value:
        return None
    return value


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

        ``role="default"`` exists only after the user configures a model.

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

        Unlike :meth:`model_for`, this falls back to the ``default`` ref
        when *role* is unconfigured.  If no default model is configured yet,
        it raises ``KeyError`` just like ``model_for("default")``.
        """
        value = getattr(self._current_used, role, None)
        if value is None:
            default = self._current_used.default
            if default is None:
                raise KeyError("No model assigned for role: 'default'")
            return default
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
            provider = self._get_provider_instance(pcfg)
            for spec in pcfg.models or []:
                is_default = (
                    default_ref is not None
                    and default_ref.provider == provider_name
                    and default_ref.model == spec.id
                )
                context_window = spec.context_window or await provider.context_window(spec.id)
                profiles.append(
                    ProfileInfo(
                        name=spec.display_name or f"{provider_name}/{spec.id}",
                        provider_type=pcfg.type,
                        model_id=spec.id,
                        context_window=context_window or _DEFAULT_CONTEXT_WINDOW,
                        is_default=is_default,
                    )
                )
        default_label = (
            f"{default_ref.provider}/{default_ref.model}"
            if default_ref is not None
            else ""
        )
        return ListProfilesResult(profiles=profiles, default_model=default_label)

    async def list_providers(
        self, ctx: HandlerContext, params: ListProvidersParams
    ) -> ListProvidersResult:
        """Return all registered providers and their models."""
        providers = []
        for name, pcfg in self._providers.items():
            model_ids = [s.id for s in (pcfg.models or [])]
            provider = self._get_provider_instance(pcfg)
            context_windows: dict[str, int] = {}
            display_names: dict[str, str] = {}
            for spec in pcfg.models or []:
                context_windows[spec.id] = (
                    spec.context_window or await provider.context_window(spec.id)
                ) or _DEFAULT_CONTEXT_WINDOW
                if spec.display_name:
                    display_names[spec.id] = spec.display_name
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
                    base_url=pcfg.base_url,
                    effective_base_url=provider_effective_base_url(pcfg.type, pcfg.base_url),
                    aws_region=pcfg.aws_region,
                    has_api_key=pcfg.api_key is not None,
                    api_key_display=secret_display(pcfg.api_key),
                    has_aws_secret_key=pcfg.aws_secret_key is not None,
                    aws_secret_key_display=secret_display(pcfg.aws_secret_key),
                    setting_fields=provider_setting_fields(pcfg.type),
                    models=model_ids,
                    context_windows=context_windows,
                    display_names=display_names,
                    roles=roles,
                )
            )
        return ListProvidersResult(
            providers=providers,
            provider_type_options=[
                ProviderTypeInfo(
                    provider_type=provider_type,
                    setting_fields=provider_setting_fields(provider_type),
                    effective_base_url=provider_effective_base_url(provider_type, None),
                )
                for provider_type in _SUPPORTED_PROVIDER_TYPES
            ],
            current_used=self._current_used_refs(),
            default_context_window=_DEFAULT_CONTEXT_WINDOW,
        )

    async def add_provider(
        self, ctx: HandlerContext, params: AddProviderParams
    ) -> AddProviderResult:
        """Add a new provider and persist to config.

        For providers that support auto-discovery (anthropic, openai_compatible,
        nvidia, deepseek), omitting ``models`` triggers discovery. For bedrock,
        ``models`` is required.
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
        if (
            self._current_used.default is not None
            and self._current_used.default.provider == params.name
        ):
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

    async def set_current_model(
        self, ctx: HandlerContext, params: SetCurrentModelParams
    ) -> SetCurrentModelResult:
        """Set one ``current_used`` role and persist."""
        if params.role not in CurrentUsedConfig.model_fields:
            raise ValueError(f"Unknown model role: {params.role!r}")

        ref = params.model
        self._validate_ref(ref)

        self._current_used = self._current_used.model_copy(update={params.role: ref})
        await self._persist()

        logger.info(
            "LLMManager: current_used.%s set to [%s, %s]",
            params.role,
            ref.provider,
            ref.model,
        )
        return SetCurrentModelResult(role=params.role, model=ref.to_list())

    async def add_model(
        self, ctx: HandlerContext, params: AddModelParams
    ) -> UpdateModelResult:
        """Add one model to an existing provider, or create a provider with it."""
        provider_name = params.provider_name.strip()
        model_id = params.model_id.strip()
        if not provider_name:
            raise ValueError("provider_name must not be empty")
        if not model_id:
            raise ValueError("model_id must not be empty")
        if params.context_window is not None and params.context_window <= 0:
            raise ValueError("context_window must be a positive integer")

        existing = self._providers.get(provider_name)
        display_name = params.display_name.strip() if params.display_name else None
        spec = ModelSpec(
            id=model_id,
            display_name=display_name or None,
            context_window=params.context_window,
        )

        if existing is None:
            provider_type = (params.provider_type or "").strip()
            if not provider_type:
                raise ValueError("provider_type is required for a new provider")
            self._provider_manager.get_provider(
                provider_type=provider_type,
                api_key=normalize_optional_text(params.api_key, None),
                base_url=normalize_optional_text(params.base_url, None),
                aws_secret_key=normalize_optional_text(params.aws_secret_key, None),
                aws_region=normalize_optional_text(params.aws_region, None),
            )
            pcfg = ProviderConfig(
                type=provider_type,
                api_key=normalize_optional_text(params.api_key, None),
                base_url=normalize_optional_text(params.base_url, None),
                aws_secret_key=normalize_optional_text(params.aws_secret_key, None),
                aws_region=normalize_optional_text(params.aws_region, None),
                models=[spec],
            )
        else:
            if self._find_model_spec(existing, model_id) is not None:
                raise ValueError(
                    f"Model '{model_id}' already exists in provider '{provider_name}'"
                )
            pcfg = existing.model_copy(update={"models": [*(existing.models or []), spec]})

        self._providers[provider_name] = pcfg
        new_ref = ModelRef(provider=provider_name, model=model_id)
        self._assign_roles(new_ref, params.roles)
        await self._persist()

        logger.info("LLMManager: added model [%s, %s]", provider_name, model_id)
        return UpdateModelResult(
            model=new_ref.to_list(),
            provider_type=pcfg.type,
            base_url=pcfg.base_url,
            effective_base_url=provider_effective_base_url(pcfg.type, pcfg.base_url),
            aws_region=pcfg.aws_region,
            has_api_key=pcfg.api_key is not None,
            api_key_display=secret_display(pcfg.api_key),
            has_aws_secret_key=pcfg.aws_secret_key is not None,
            aws_secret_key_display=secret_display(pcfg.aws_secret_key),
            setting_fields=provider_setting_fields(pcfg.type),
            display_name=spec.display_name,
            context_window=spec.context_window,
            roles=self._roles_for_ref(new_ref),
        )

    async def update_model(
        self, ctx: HandlerContext, params: UpdateModelParams
    ) -> UpdateModelResult:
        """Update one provider model's display settings and current-used roles."""
        ref = params.model
        old_pcfg = self._providers.get(ref.provider)
        if old_pcfg is None:
            raise ModelNotFoundError(
                f"{ref.provider}/{ref.model}",
                known=self._all_model_keys(),
            )
        spec = self._find_model_spec_for_ref(ref)

        new_provider = (params.provider_name or ref.provider).strip()
        new_model_id = (params.model_id or ref.model).strip()
        if not new_provider:
            raise ValueError("provider_name must not be empty")
        if not new_model_id:
            raise ValueError("model_id must not be empty")
        new_ref = ModelRef(provider=new_provider, model=new_model_id)
        if new_provider != ref.provider and new_provider in self._providers:
            raise ValueError(f"Provider '{new_provider}' already exists")
        if new_model_id != ref.model and self._find_model_spec(old_pcfg, new_model_id) is not None:
            raise ValueError(
                f"Model '{new_model_id}' already exists in provider '{ref.provider}'"
            )

        updates: dict[str, object] = {}
        display_name = params.display_name.strip() if params.display_name else None
        updates["id"] = new_model_id
        updates["display_name"] = display_name or None
        updates["context_window"] = params.context_window

        if params.context_window is not None and params.context_window <= 0:
            raise ValueError("context_window must be a positive integer")

        updated_spec = spec.model_copy(update=updates)
        provider_updates: dict[str, object] = {
            "type": (params.provider_type or old_pcfg.type).strip(),
            "base_url": normalize_optional_text(params.base_url, old_pcfg.base_url),
            "aws_region": normalize_optional_text(params.aws_region, old_pcfg.aws_region),
        }
        if not provider_updates["type"]:
            raise ValueError("provider_type must not be empty")
        if params.api_key is not None:
            provider_updates["api_key"] = params.api_key.strip() or None
        if params.aws_secret_key is not None:
            provider_updates["aws_secret_key"] = params.aws_secret_key.strip() or None

        models = [
            updated_spec if model_spec.id == ref.model else model_spec
            for model_spec in (old_pcfg.models or [])
        ]
        provider_updates["models"] = models
        new_pcfg = old_pcfg.model_copy(update=provider_updates)
        if new_provider != ref.provider:
            del self._providers[ref.provider]
        self._providers[new_provider] = new_pcfg
        self._retarget_refs(ref, new_ref)

        self._assign_roles(new_ref, params.roles)

        await self._persist()

        logger.info("LLMManager: updated model [%s, %s]", new_ref.provider, new_ref.model)
        return UpdateModelResult(
            model=new_ref.to_list(),
            provider_type=new_pcfg.type,
            base_url=new_pcfg.base_url,
            effective_base_url=provider_effective_base_url(new_pcfg.type, new_pcfg.base_url),
            aws_region=new_pcfg.aws_region,
            has_api_key=new_pcfg.api_key is not None,
            api_key_display=secret_display(new_pcfg.api_key),
            has_aws_secret_key=new_pcfg.aws_secret_key is not None,
            aws_secret_key_display=secret_display(new_pcfg.aws_secret_key),
            setting_fields=provider_setting_fields(new_pcfg.type),
            display_name=updated_spec.display_name,
            context_window=updated_spec.context_window,
            roles=self._roles_for_ref(new_ref),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _assign_roles(self, ref: ModelRef, roles: list[str] | None) -> None:
        """Assign exact current-used roles to a model when roles are provided."""
        if roles is None:
            return
        requested_roles = set(roles)
        unknown = requested_roles.difference(CurrentUsedConfig.model_fields)
        if unknown:
            raise ValueError(f"Unknown model role: {sorted(unknown)[0]!r}")

        current_updates: dict[str, ModelRef | None] = {}
        for role_name, role_ref in self._current_used.model_dump().items():
            existing = ModelRef.model_validate(role_ref) if role_ref is not None else None
            if role_name in requested_roles:
                current_updates[role_name] = ref
            elif existing == ref:
                current_updates[role_name] = None
        self._current_used = self._current_used.model_copy(update=current_updates)

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

    def _find_model_spec_for_ref(self, ref: ModelRef) -> ModelSpec:
        """Find a ModelSpec by provider/model ref or raise ModelNotFoundError."""
        pcfg = self._providers.get(ref.provider)
        if pcfg is None:
            raise ModelNotFoundError(
                f"{ref.provider}/{ref.model}",
                known=self._all_model_keys(),
            )
        spec = self._find_model_spec(pcfg, ref.model)
        if spec is None:
            raise ModelNotFoundError(
                f"{ref.provider}/{ref.model}",
                known=self._all_model_keys(),
            )
        return spec

    def _replace_model_spec(self, ref: ModelRef, updated_spec: ModelSpec) -> None:
        """Replace one ModelSpec in the in-memory provider table."""
        pcfg = self._providers.get(ref.provider)
        if pcfg is None or pcfg.models is None:
            raise ModelNotFoundError(
                f"{ref.provider}/{ref.model}",
                known=self._all_model_keys(),
            )
        models = [
            updated_spec if spec.id == ref.model else spec
            for spec in pcfg.models
        ]
        self._providers[ref.provider] = pcfg.model_copy(update={"models": models})

    def _retarget_refs(self, old_ref: ModelRef, new_ref: ModelRef) -> None:
        """Retarget current-used roles and aliases after provider/model rename."""
        current_updates: dict[str, ModelRef] = {}
        for role_name, role_ref in self._current_used.model_dump().items():
            if role_ref is None:
                continue
            if ModelRef.model_validate(role_ref) == old_ref:
                current_updates[role_name] = new_ref
        if current_updates:
            self._current_used = self._current_used.model_copy(update=current_updates)

        for alias, alias_ref in list(self._aliases.items()):
            if alias_ref == old_ref:
                self._aliases[alias] = new_ref

    def _roles_for_ref(self, ref: ModelRef) -> list[str]:
        """Return current-used role names assigned to a model ref."""
        roles: list[str] = []
        for role_name, role_ref in self._current_used.model_dump().items():
            if role_ref is None:
                continue
            if ModelRef.model_validate(role_ref) == ref:
                roles.append(role_name)
        return sorted(roles)

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

    def _current_used_refs(self) -> dict[str, list[str]]:
        """Return non-empty current-used role assignments for API responses."""
        refs: dict[str, list[str]] = {}
        for role_name, ref in self._current_used.model_dump().items():
            if ref is None:
                continue
            refs[role_name] = ModelRef.model_validate(ref).to_list()
        return refs

    async def _persist(self) -> None:
        """Rebuild LLMConfig from current in-memory state and write to disk."""
        new_config = LLMConfig(
            providers=dict(self._providers),
            current_used=self._current_used,
            model_aliases=dict(self._aliases),
        )
        await self._cfg_section.update(new_config)
