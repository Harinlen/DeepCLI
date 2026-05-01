"""Schema contracts for Agent Control Plane Batch B0.

These models are shared contract shapes only.  They deliberately avoid
process startup, network IO, FastAPI imports, and subsystem wiring.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kernel.agents.control_plane import AgentRuntimeKind, AgentStatus


class AgentRole(StrEnum):
    """Durable Agent roles known to Mustang."""

    primary = "primary"
    session = "session"


class AgentRuntimeSpec(BaseModel):
    """Declarative runtime backend for an AgentDefinition."""

    model_config = ConfigDict(extra="forbid")

    kind: AgentRuntimeKind
    command: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    endpoint: str | None = None
    profile: str | None = None

    @model_validator(mode="after")
    def _command_required_for_process_runtimes(self) -> AgentRuntimeSpec:
        if self.kind in {AgentRuntimeKind.child_kernel, AgentRuntimeKind.external_acp} and not self.command:
            raise ValueError("command is required for process-backed runtimes")
        return self


class AgentPolicySpec(BaseModel):
    """Policy profile references for a durable agent."""

    model_config = ConfigDict(extra="forbid")

    management_capabilities: tuple[str, ...] = ()
    tool_policy_profile: str | None = None
    platform_binding_policy: str | None = None


class PlatformBindingSpec(BaseModel):
    """Declarative external-platform binding owned by AgentDefinition."""

    model_config = ConfigDict(extra="forbid")

    adapter_id: str
    platform: str
    account_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None
    enabled: bool = True


class AgentBindingSpec(BaseModel):
    """Native and platform routing declarations for an agent."""

    model_config = ConfigDict(extra="forbid")

    native_default: bool = False
    platforms: tuple[PlatformBindingSpec, ...] = ()


class AgentResourceSpec(BaseModel):
    """Resource scope/profile references resolved by Agent Runtime Managers."""

    model_config = ConfigDict(extra="forbid")

    memory_scopes: tuple[str, ...] = ("global", "workspace", "agent")
    skill_scopes: tuple[str, ...] = ("global", "workspace", "agent")
    mcp_scopes: tuple[str, ...] = ("global", "workspace", "agent")
    hook_profile: str | None = None
    model_profile: str | None = None
    prompt_profile: str | None = None


class AgentDefinition(BaseModel):
    """ConfigManager-owned declarative durable agent configuration."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(alias="id")
    name: str
    role: AgentRole
    workspace: str
    state_dir: str
    runtime: AgentRuntimeSpec
    policy: AgentPolicySpec = Field(default_factory=AgentPolicySpec)
    bindings: AgentBindingSpec = Field(default_factory=AgentBindingSpec)
    resources: AgentResourceSpec = Field(default_factory=AgentResourceSpec)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeRecord(BaseModel):
    """Manager/Supervisor-owned live state, never ConfigManager truth."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    runtime_kind: AgentRuntimeKind
    process_id: int | None = None
    websocket_endpoint: str | None = None
    status: AgentStatus
    heartbeat_at: str | None = None
    started_at: str | None = None
    restart_count: int = 0
    queue_depth: int = 0
    active_turn_id: str | None = None
    last_exit_code: int | None = None
    last_error: str | None = None


class CallerKind(StrEnum):
    """Caller identity families that can enter the Agent Hub."""

    access_client = "access_client"
    platform_adapter = "platform_adapter"
    durable_agent = "durable_agent"
    supervisor = "supervisor"


class CallerIdentity(BaseModel):
    """Identity propagated from Access Agent or Agent Runtime to the Hub."""

    model_config = ConfigDict(extra="forbid")

    kind: CallerKind
    subject_id: str
    connection_id: str | None = None
    agent_id: str | None = None
    platform: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegistrationToken(BaseModel):
    """Supervisor-issued short-lived token for Agent Runtime registration."""

    model_config = ConfigDict(extra="forbid")

    token_id: str
    agent_id: str
    issued_at: str
    expires_at: str
    issuer: Literal["supervisor"] = "supervisor"


class ManagementCapability(BaseModel):
    """Capability attached to a management call."""

    model_config = ConfigDict(extra="forbid")

    caller: CallerIdentity
    capability: str
    agent_id: str

    @field_validator("caller")
    @classmethod
    def _caller_must_be_durable_agent(cls, value: CallerIdentity) -> CallerIdentity:
        if value.kind is not CallerKind.durable_agent:
            raise ValueError("management capability requires a durable agent caller")
        return value


class RoutingContext(BaseModel):
    """Routing attributes carried by Access Agent or Agent Runtime."""

    model_config = ConfigDict(extra="forbid")

    native_default: bool = False
    platform: str | None = None
    account_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None


class ReplySink(BaseModel):
    """Opaque Access Agent reply target."""

    model_config = ConfigDict(extra="forbid")

    sink_id: str
    kind: Literal["native_ws", "platform_adapter"]


class RouterFrame(BaseModel):
    """Message frame sent through Agent Hub.Router."""

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str | None = None
    caller: CallerIdentity
    routing: RoutingContext = Field(default_factory=RoutingContext)
    session_id: str | None = None
    message_kind: Literal["prompt", "message", "update", "status", "error"]
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    reply_sink: ReplySink | None = None


class BindingPlanEntry(BaseModel):
    """Materialized adapter binding emitted by Manager to Access Agent."""

    model_config = ConfigDict(extra="forbid")

    adapter_id: str
    platform: str
    target_agent_id: str
    account_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None
    enabled: bool = True


class BindingPlan(BaseModel):
    """Revisioned platform binding plan for Access Agent."""

    model_config = ConfigDict(extra="forbid")

    revision: int
    entries: tuple[BindingPlanEntry, ...] = ()


class RoutingSnapshot(BaseModel):
    """Read-only routing table pushed from Manager to Router."""

    model_config = ConfigDict(extra="forbid")

    revision: int
    default_agent_id: str
    agent_ids: tuple[str, ...]
    platform_bindings: tuple[BindingPlanEntry, ...] = ()
