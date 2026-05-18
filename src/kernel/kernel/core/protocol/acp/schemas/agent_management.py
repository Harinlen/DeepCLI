"""ACP schemas for Agent and channel management extension methods."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ManagementBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class EmptyManagementRequest(ManagementBaseModel):
    pass


class AgentListResponse(ManagementBaseModel):
    agents: list[dict[str, Any]]


class AgentAddRequest(ManagementBaseModel):
    id: str
    name: str | None = None
    workspace: str | None = None
    state_dir: str | None = None
    role: str = "session"
    runtime_kind: str = "in_process_session_agent"


class AgentMutationResponse(ManagementBaseModel):
    agent: dict[str, Any] | None = None
    deleted: bool | None = None


class AgentSetIdentityRequest(ManagementBaseModel):
    agent_id: str = Field(alias="agentId")
    name: str | None = None
    workspace: str | None = None


class AgentBindingsRequest(ManagementBaseModel):
    agent_id: str | None = Field(default=None, alias="agentId")


class AgentBindingsResponse(ManagementBaseModel):
    bindings: list[dict[str, Any]]


class AgentBindRequest(ManagementBaseModel):
    agent_id: str = Field(alias="agentId")
    adapter_id: str = Field(alias="adapterId")
    platform: str
    account_id: str | None = Field(default=None, alias="accountId")
    context: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class AgentUnbindRequest(ManagementBaseModel):
    agent_id: str = Field(alias="agentId")
    adapter_id: str = Field(alias="adapterId")
    platform: str
    account_id: str | None = Field(default=None, alias="accountId")
    context: dict[str, str] = Field(default_factory=dict)


class AgentDeleteRequest(ManagementBaseModel):
    agent_id: str = Field(alias="agentId")


class ChannelListResponse(ManagementBaseModel):
    channels: list[dict[str, Any]]


class ChannelStatusResponse(ManagementBaseModel):
    status: list[dict[str, Any]]


class ChannelCapabilitiesResponse(ManagementBaseModel):
    capabilities: list[dict[str, Any]]


class ChannelResolveRequest(ManagementBaseModel):
    adapter_id: str = Field(alias="adapterId")
    peer_id: str = Field(alias="peerId")
    thread_id: str | None = Field(default=None, alias="threadId")


class ChannelResolveResponse(ManagementBaseModel):
    session_id: str | None = Field(default=None, alias="sessionId")


class ChannelOperationRequest(ManagementBaseModel):
    adapter_id: str | None = Field(default=None, alias="adapterId")
    payload: dict[str, Any] = Field(default_factory=dict)


class ChannelOperationResponse(ManagementBaseModel):
    ok: bool
    message: str
