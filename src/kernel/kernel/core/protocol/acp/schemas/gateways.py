"""ACP schemas for Access Router gateway management commands."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from kernel.core.protocol.acp.schemas.base import AcpModel


class GatewaysListRequest(AcpModel):
    actor_agent_id: str = "primary"


class GatewaysListResponse(AcpModel):
    gateways: list[dict[str, Any]]


class GatewayCreateRequest(AcpModel):
    actor_agent_id: str = "primary"
    gateway_id: str
    gateway_type: str = "test"
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class GatewayRecordResponse(AcpModel):
    gateway: dict[str, Any]


class GatewaysStatusRequest(AcpModel):
    actor_agent_id: str = "primary"
    gateway_id: str | None = None


class GatewaysStatusResponse(AcpModel):
    status: list[dict[str, Any]]


class GatewayIdRequest(AcpModel):
    actor_agent_id: str = "primary"
    gateway_id: str


class GatewayRevisionResponse(AcpModel):
    gateway_id: str
    revision: int


class GatewayDeleteRequest(AcpModel):
    actor_agent_id: str = "primary"
    gateway_id: str
    confirm: bool = False


class GatewayDeleteResponse(AcpModel):
    gateway_id: str
    deleted: bool
    revision: int
    disabled_bindings: int


class GatewayReloadRequest(AcpModel):
    actor_agent_id: str = "primary"
    gateway_id: str
    fail: bool = False


class GatewayReloadResponse(AcpModel):
    gateway_id: str
    status: str
    error: str | None = None


class GatewayBindingsRequest(AcpModel):
    actor_agent_id: str = "primary"
    gateway_id: str | None = None
    agent_id: str | None = None


class GatewayBindingsResponse(AcpModel):
    bindings: list[dict[str, Any]]


class GatewayBindRequest(AcpModel):
    actor_agent_id: str = "primary"
    gateway_id: str
    channel_key: str
    agent_id: str
    session_id: str | None = None


class GatewayBindResponse(AcpModel):
    binding: dict[str, Any]


class GatewayUnbindRequest(AcpModel):
    actor_agent_id: str = "primary"
    binding_id: str


class GatewayUnbindResponse(AcpModel):
    unbound: bool
