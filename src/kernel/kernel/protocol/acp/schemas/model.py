"""ACP wire-format schemas for ``model/*`` methods.

These are ACP-specific (camelCase wire format, ``AcpModel`` base).
The routing layer translates them to mustang contract types before
calling ``ModelHandler``.
"""

from __future__ import annotations

from typing import Any

from kernel.protocol.acp.schemas.base import AcpModel


# ---------------------------------------------------------------------------
# model/profile_list
# ---------------------------------------------------------------------------


class ListProfilesRequest(AcpModel):
    """``model/profile_list`` request params (empty)."""

    meta: dict[str, Any] | None = None


class AcpProfileEntry(AcpModel):
    """Wire representation of one provider×model profile."""

    name: str
    provider_type: str
    model_id: str
    is_default: bool


class ListProfilesResponse(AcpModel):
    """``model/profile_list`` response."""

    profiles: list[AcpProfileEntry]
    default_model: str


# ---------------------------------------------------------------------------
# model/provider_list
# ---------------------------------------------------------------------------


class ListProvidersRequest(AcpModel):
    """``model/provider_list`` request params (empty)."""

    meta: dict[str, Any] | None = None


class AcpProviderEntry(AcpModel):
    """Wire representation of one provider."""

    name: str
    provider_type: str
    models: list[str]
    roles: dict[str, bool]


class ListProvidersResponse(AcpModel):
    """``model/provider_list`` response."""

    providers: list[AcpProviderEntry]
    default_model: list[str]


# ---------------------------------------------------------------------------
# model/provider_add
# ---------------------------------------------------------------------------


class AddProviderRequest(AcpModel):
    """``model/provider_add`` request params."""

    name: str
    provider_type: str
    api_key: str | None = None
    base_url: str | None = None
    aws_secret_key: str | None = None
    aws_region: str | None = None
    models: list[str] | None = None

    meta: dict[str, Any] | None = None


class AddProviderResponse(AcpModel):
    """``model/provider_add`` response."""

    name: str
    models: list[str]


# ---------------------------------------------------------------------------
# model/provider_remove
# ---------------------------------------------------------------------------


class RemoveProviderRequest(AcpModel):
    """``model/provider_remove`` request params."""

    name: str
    meta: dict[str, Any] | None = None


class RemoveProviderResponse(AcpModel):
    """``model/provider_remove`` response (empty)."""


# ---------------------------------------------------------------------------
# model/provider_refresh
# ---------------------------------------------------------------------------


class RefreshModelsRequest(AcpModel):
    """``model/provider_refresh`` request params."""

    name: str
    meta: dict[str, Any] | None = None


class RefreshModelsResponse(AcpModel):
    """``model/provider_refresh`` response."""

    models: list[str]


# ---------------------------------------------------------------------------
# model/set_default
# ---------------------------------------------------------------------------


class SetDefaultModelRequest(AcpModel):
    """``model/set_default`` request params."""

    provider: str
    model: str

    meta: dict[str, Any] | None = None


class SetDefaultModelResponse(AcpModel):
    """``model/set_default`` response."""

    default_model: list[str]
