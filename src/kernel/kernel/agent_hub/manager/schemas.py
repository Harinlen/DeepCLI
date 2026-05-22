"""Pydantic schemas for ResourceStore-backed AgentManager."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class GrantCapability(StrEnum):
    """Management capabilities granted to agents."""

    GLOBAL_RESOURCE_WRITE = "global_resource_write"
    AGENT_CONTROL = "agent_control"


class ResourceScope(StrEnum):
    """Grant resource scopes."""

    GLOBAL = "global"
    AGENT = "agent"
    WORKSPACE = "workspace"


class AgentRuntimeSpec(BaseModel):
    """Runtime launch declaration."""

    kind: str = "mustang"
    command: tuple[str, ...] = ()
    autostart: bool = False


class AgentDefinitionRecord(BaseModel):
    """Durable Agent definition row."""

    agent_id: str
    name: str
    identity: dict[str, object] = Field(default_factory=dict)
    workspace: str
    state_dir: str
    runtime: AgentRuntimeSpec = Field(default_factory=AgentRuntimeSpec)
    status: str = "active"
    deleted_at: str | None = None
    state_dir_deletion_status: str | None = None
    revision: int = 1
    updated_at: str
    updated_by_agent_id: str | None = None


class AgentSummary(BaseModel):
    """List projection for one Agent."""

    agent_id: str
    name: str
    status: str
    revision: int


class RuntimeStatus(BaseModel):
    """Runtime lifecycle status."""

    agent_id: str
    desired_state: str
    observed_state: str
    route_status: str | None = None
    pid: int | None = None
    process_running: bool = False
    runtime_heartbeat_fresh: bool | None = None
    heartbeat_age_seconds: float | None = None
    healthy: bool = False


class AgentHealth(BaseModel):
    """Aggregated runtime health."""

    agent_id: str
    healthy: bool
    reason: str
    process_running: bool = False
    route_status: str | None = None
    runtime_heartbeat_fresh: bool | None = None
    heartbeat_age_seconds: float | None = None


class ManagementGrant(BaseModel):
    """Management grant row."""

    grant_id: str
    subject_agent_id: str
    capability: GrantCapability
    resource_scope: ResourceScope
    resource_id: str | None = None
    owner_agent_id: str | None = None
    workspace: str | None = None
    granted_by_agent_id: str
    granted_at: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None


class DeleteAgentResult(BaseModel):
    """Delete result preserving workspace by default."""

    agent_id: str
    deleted: bool
    workspace_deleted: bool = False
    state_dir_deletion_status: str | None = None
    state_dir_cleanup_error: str | None = None


class AgentDirectorySnapshot(BaseModel):
    """Routing directory snapshot."""

    revision: int
    agents: tuple[AgentSummary, ...]


class CreateAgentSpec(BaseModel):
    """Input shape for creating an Agent."""

    agent_id: str
    name: str
    identity: dict[str, object] = Field(default_factory=dict)
    workspace: Path
    state_dir: Path
    runtime: AgentRuntimeSpec = Field(default_factory=AgentRuntimeSpec)
