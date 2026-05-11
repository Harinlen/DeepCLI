"""ACP wire-format schemas for WebFetch management methods."""

from __future__ import annotations

from typing import Any

from kernel.core.protocol.acp.schemas.base import AcpModel


class WebFetchBackendOptionsRequest(AcpModel):
    meta: dict[str, Any] | None = None


class WebFetchBackendOption(AcpModel):
    id: str
    label: str
    category: str
    cost: str
    role: str
    installed: bool
    has_credentials: bool
    available: bool
    setup_required: bool
    setup_plan: dict[str, Any] | None = None
    credential_required: bool = False
    credential_request: dict[str, Any] | None = None
    current: bool = False


class WebFetchBackendOptionsResponse(AcpModel):
    current: str
    options: list[WebFetchBackendOption]


class SetWebFetchBackendRequest(AcpModel):
    backend: str
    run_setup: bool = False
    api_key: str | None = None
    meta: dict[str, Any] | None = None


class SetWebFetchBackendResponse(AcpModel):
    backend: str
    changed: bool
    setup_required: bool = False
    setup_plan: dict[str, Any] | None = None
    setup_result: dict[str, Any] | None = None
    credential_required: bool = False
    credential_request: dict[str, Any] | None = None
    message: str | None = None


class WebFetchConfigRequest(AcpModel):
    meta: dict[str, Any] | None = None


class WebFetchConfigResponse(AcpModel):
    backend: str
    backends: dict[str, dict[str, Any]]


class SetWebFetchConfigRequest(AcpModel):
    path: str
    value: Any = None
    meta: dict[str, Any] | None = None


class SetWebFetchConfigResponse(AcpModel):
    backend: str
    backends: dict[str, dict[str, Any]]


__all__ = [
    "SetWebFetchBackendRequest",
    "SetWebFetchBackendResponse",
    "SetWebFetchConfigRequest",
    "SetWebFetchConfigResponse",
    "WebFetchBackendOption",
    "WebFetchBackendOptionsRequest",
    "WebFetchBackendOptionsResponse",
    "WebFetchConfigRequest",
    "WebFetchConfigResponse",
]
