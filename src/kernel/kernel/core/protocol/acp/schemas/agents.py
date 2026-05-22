"""ACP schemas for Agent and Agent-message management commands."""

from __future__ import annotations

from typing import Any

from kernel.core.protocol.acp.schemas.base import AcpModel


class AgentsListRequest(AcpModel):
    actor_agent_id: str = "primary"
    include_bindings: bool = False


class AgentsListResponse(AcpModel):
    agents: list[dict[str, Any]]
    bindings: list[dict[str, Any]] | None = None


class AgentsAddRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str
    workspace: str
    name: str | None = None
    state_dir: str | None = None


class AgentRecordResponse(AcpModel):
    agent: dict[str, Any]


class AgentsDeleteRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str
    confirm: bool = False


class AgentsDeleteResponse(AcpModel):
    agent_id: str
    deleted: bool
    workspace_deleted: bool = False
    state_dir_deletion_status: str | None = None
    state_dir_cleanup_error: str | None = None


class AgentsSetIdentityRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str
    name: str | None = None
    avatar: str | None = None
    theme: str | None = None
    identity_patch: dict[str, Any] | None = None


class AgentsBindingsRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str | None = None


class AgentsBindingsResponse(AcpModel):
    bindings: list[dict[str, Any]]


class AgentsBindRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str
    bind: str
    session_id: str | None = None


class AgentsBindResponse(AcpModel):
    binding: dict[str, Any]


class AgentsUnbindRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str
    bind: str | None = None
    all: bool = False


class AgentsUnbindResponse(AcpModel):
    removed: int


class AgentLifecycleRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str
    router_endpoint: str | None = None
    router_token: str | None = None


class AgentLifecycleResponse(AcpModel):
    status: dict[str, Any]


class AgentHealthRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str


class AgentHealthResponse(AcpModel):
    health: dict[str, Any]


class AgentsGrantsRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str | None = None


class AgentsGrantsResponse(AcpModel):
    grants: list[dict[str, Any]]


class AgentsGrantRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str
    capability: str
    scope: str = "global"
    resource: str | None = None
    workspace: str | None = None
    expires_at: str | None = None


class AgentsGrantResponse(AcpModel):
    grant: dict[str, Any]


class AgentsRevokeGrantRequest(AcpModel):
    actor_agent_id: str = "primary"
    grant_id: str


class AgentSendRequest(AcpModel):
    actor_agent_id: str = "primary"
    agent_id: str
    message: str
    session_id: str | None = None
    deliver: bool = True


class AgentSendResponse(AcpModel):
    delivered: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None
