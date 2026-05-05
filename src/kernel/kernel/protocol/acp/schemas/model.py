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
    context_window: int | None = None
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
    base_url: str | None = None
    effective_base_url: str | None = None
    aws_region: str | None = None
    has_api_key: bool = False
    api_key_display: str | None = None
    has_aws_secret_key: bool = False
    aws_secret_key_display: str | None = None
    setting_fields: list[str]
    models: list[str]
    context_windows: dict[str, int]
    display_names: dict[str, str]
    roles: dict[str, bool]


class AcpProviderTypeEntry(AcpModel):
    """Wire representation of one supported provider backend type."""

    provider_type: str
    setting_fields: list[str]
    effective_base_url: str | None = None


class ListProvidersResponse(AcpModel):
    """``model/provider_list`` response."""

    providers: list[AcpProviderEntry]
    provider_type_options: list[AcpProviderTypeEntry]
    current_used: dict[str, list[str]]
    default_context_window: int


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
# model/set_current
# ---------------------------------------------------------------------------


class SetCurrentModelRequest(AcpModel):
    """``model/set_current`` request params."""

    provider: str
    model: str
    role: str = "default"

    meta: dict[str, Any] | None = None


class SetCurrentModelResponse(AcpModel):
    """``model/set_current`` response."""

    role: str
    model: list[str]


# ---------------------------------------------------------------------------
# model/update
# ---------------------------------------------------------------------------


class AddModelRequest(AcpModel):
    """``model/add`` request params."""

    provider_name: str
    provider_type: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    aws_secret_key: str | None = None
    aws_region: str | None = None
    model_id: str
    display_name: str | None = None
    context_window: int | None = None
    roles: list[str] | None = None

    meta: dict[str, Any] | None = None


class UpdateModelRequest(AcpModel):
    """``model/update`` request params."""

    provider: str
    model: str
    provider_name: str | None = None
    provider_type: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    aws_secret_key: str | None = None
    aws_region: str | None = None
    model_id: str | None = None
    display_name: str | None = None
    context_window: int | None = None
    roles: list[str] | None = None

    meta: dict[str, Any] | None = None


class UpdateModelResponse(AcpModel):
    """``model/update`` response."""

    model: list[str]
    provider_type: str
    base_url: str | None
    effective_base_url: str | None
    aws_region: str | None
    has_api_key: bool
    api_key_display: str | None
    has_aws_secret_key: bool
    aws_secret_key_display: str | None
    setting_fields: list[str]
    display_name: str | None
    context_window: int | None
    roles: list[str]
