"""ModelHandler -- the contract the LLM management layer must fulfil.

The protocol layer routes every inbound ``model/*`` ACP method to one
of these methods after deserialising params into the corresponding
contract type.  The LLM layer returns a typed result; the protocol
layer serialises it back into a JSON-RPC response frame.

Isolation guarantee
-------------------
Implementations MUST NOT import anything from ``kernel.core.protocol.acp``
or reference JSON-RPC concepts.  The seam is purely Pydantic objects
in, Pydantic objects out.

These are **kernel-global** operations (they mutate the shared provider
registry and persist to config), in contrast to ``SessionHandler``
methods which are session-scoped.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kernel.core.protocol.interfaces.contracts.add_provider_params import (
    AddProviderParams,
)
from kernel.core.protocol.interfaces.contracts.add_provider_result import (
    AddProviderResult,
)
from kernel.core.protocol.interfaces.contracts.add_model_params import AddModelParams
from kernel.core.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.core.protocol.interfaces.contracts.get_thinking_params import GetThinkingParams
from kernel.core.protocol.interfaces.contracts.get_thinking_result import GetThinkingResult
from kernel.core.protocol.interfaces.contracts.list_profiles_params import (
    ListProfilesParams,
)
from kernel.core.protocol.interfaces.contracts.list_profiles_result import (
    ListProfilesResult,
)
from kernel.core.protocol.interfaces.contracts.list_providers_params import (
    ListProvidersParams,
)
from kernel.core.protocol.interfaces.contracts.list_providers_result import (
    ListProvidersResult,
)
from kernel.core.protocol.interfaces.contracts.refresh_models_params import (
    RefreshModelsParams,
)
from kernel.core.protocol.interfaces.contracts.refresh_models_result import (
    RefreshModelsResult,
)
from kernel.core.protocol.interfaces.contracts.remove_provider_params import (
    RemoveProviderParams,
)
from kernel.core.protocol.interfaces.contracts.remove_provider_result import (
    RemoveProviderResult,
)
from kernel.core.protocol.interfaces.contracts.set_current_model_params import (
    SetCurrentModelParams,
)
from kernel.core.protocol.interfaces.contracts.set_current_model_result import (
    SetCurrentModelResult,
)
from kernel.core.protocol.interfaces.contracts.set_thinking_params import SetThinkingParams
from kernel.core.protocol.interfaces.contracts.set_thinking_result import SetThinkingResult
from kernel.core.protocol.interfaces.contracts.update_model_params import UpdateModelParams
from kernel.core.protocol.interfaces.contracts.update_model_result import UpdateModelResult


@runtime_checkable
class ModelHandler(Protocol):
    """Contract implemented by ``LLMManager``."""

    async def list_profiles(
        self, ctx: HandlerContext, params: ListProfilesParams
    ) -> ListProfilesResult:
        """Return all provider×model combinations as flat profile entries."""
        ...

    async def list_providers(
        self, ctx: HandlerContext, params: ListProvidersParams
    ) -> ListProvidersResult:
        """Return all registered providers and their models."""
        ...

    async def get_thinking(
        self, ctx: HandlerContext, params: GetThinkingParams
    ) -> GetThinkingResult:
        """Return the kernel-wide LLM thinking setting."""
        ...

    async def set_thinking(
        self, ctx: HandlerContext, params: SetThinkingParams
    ) -> SetThinkingResult:
        """Persist the kernel-wide LLM thinking setting."""
        ...

    async def add_provider(
        self, ctx: HandlerContext, params: AddProviderParams
    ) -> AddProviderResult:
        """Add a new provider and persist it to config.

        Raises ``ValueError`` if a provider with the same name exists.
        Raises ``ValueError`` for unknown ``provider_type`` values.
        """
        ...

    async def remove_provider(
        self, ctx: HandlerContext, params: RemoveProviderParams
    ) -> RemoveProviderResult:
        """Remove a provider by name and persist the change.

        Raises ``ValueError`` if the provider does not exist.
        Raises ``ValueError`` if removing it would leave zero providers.
        """
        ...

    async def refresh_models(
        self, ctx: HandlerContext, params: RefreshModelsParams
    ) -> RefreshModelsResult:
        """Re-discover models for a provider and persist.

        Raises ``ValueError`` if the provider does not exist or
        discovery returns no models.
        """
        ...

    async def set_current_model(
        self, ctx: HandlerContext, params: SetCurrentModelParams
    ) -> SetCurrentModelResult:
        """Set one ``llm.current_used`` role and persist the change.

        Raises ``ModelNotFoundError`` if the ref does not resolve to
        a known provider/model.
        Raises ``ValueError`` if the role is unknown.
        """
        ...

    async def add_model(self, ctx: HandlerContext, params: AddModelParams) -> UpdateModelResult:
        """Add a model to an existing provider or create a provider with one model.

        Raises ``ValueError`` if the provider is new and ``provider_type`` is
        missing, if the model already exists, or if a requested role is unknown.
        """
        ...

    async def update_model(
        self, ctx: HandlerContext, params: UpdateModelParams
    ) -> UpdateModelResult:
        """Update one provider model's display settings and roles.

        Raises ``ModelNotFoundError`` if the ref does not resolve to
        a known provider/model.
        Raises ``ValueError`` if a requested role is unknown.
        """
        ...
