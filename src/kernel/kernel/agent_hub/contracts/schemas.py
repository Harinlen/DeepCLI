"""Shared Agent Control Plane schemas.

These types are contract-only B0 groundwork.  They describe the future
Supervisor / Agent Hub / Access Agent / Agent Runtime boundary without wiring
new runtime behavior into the existing single-primary path.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kernel.agent_hub.contracts.control_plane import AgentRuntimeKind, AgentStatus


PRIMARY_AGENT_ID = "primary"
DEFAULT_AGENT_ROOT = "agents"
AGENT_CONTRACT_SCHEMA_VERSION = "agent-control-plane.b0"


class StrictSchema(BaseModel):
    """Base model for closed wire contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentRole(StrEnum):
    """Durable agent role in the Mustang network."""

    PRIMARY = "primary"
    SESSION = "session"


class CallerIdentityKind(StrEnum):
    """Identity source normalized before frames enter Agent Hub."""

    ACCESS = "access"
    PLATFORM = "platform"
    DURABLE_AGENT = "durable_agent"
    SUPERVISOR = "supervisor"


class ManagementCapability(StrEnum):
    """Capabilities that permit calls into Agent Hub.Manager."""

    AGENT_CREATE = "agent.create"
    AGENT_DELETE = "agent.delete"
    AGENT_STATUS = "agent.status"
    AGENT_CONTROL = "agent.control"
    GLOBAL_RESOURCE_WRITE = "global_resource.write"


class AgentRuntimeContract(StrEnum):
    """Contracts that Agent Hub may forward to a registered runtime."""

    PROMPT = "agent.prompt"
    ACTIVATE_SKILL = "agent.activate_skill"
    COMMANDS_LIST = "agent.commands_list"
    SESSION_NEW = "agent.session_new"
    SESSION_LIST = "agent.session_list"
    SESSION_LOAD = "agent.session_load"
    RESUME = "agent.resume"
    CANCEL = "agent.cancel"
    EXECUTE_SHELL = "agent.execute_shell"
    EXECUTE_PYTHON = "agent.execute_python"
    CANCEL_EXECUTION = "agent.cancel_execution"
    SET_MODE = "agent.set_mode"
    GET_USAGE = "agent.get_usage"
    CLOSE = "agent.close"
    MODEL_REQUEST = "agent.model_request"
    TOOLS_REQUEST = "agent.tools_request"


AGENT_RUNTIME_STREAMING_CONTRACTS = frozenset(
    {
        AgentRuntimeContract.PROMPT,
        AgentRuntimeContract.ACTIVATE_SKILL,
    }
)
AGENT_RUNTIME_FORWARDED_CONTRACTS = frozenset(AgentRuntimeContract)


class RouterFrameKind(StrEnum):
    """Router message-plane frame kinds.

    Management operations are intentionally absent.  Agent lifecycle and
    topology changes must use Manager contracts, not Router frames.
    """

    USER_MESSAGE = "user_message"
    AGENT_MESSAGE = "agent_message"
    AGENT_UPDATE = "agent_update"


class AgentRuntimeDeclaration(StrictSchema):
    """Declared runtime backend for an AgentDefinition."""

    kind: AgentRuntimeKind = AgentRuntimeKind.in_process_session_agent
    command: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    endpoint: str | None = None
    profile: str | None = None


class AgentPolicy(StrictSchema):
    """Declared policy defaults for a durable agent."""

    management_capabilities: tuple[ManagementCapability, ...] = ()
    tool_policy_profile: str | None = None
    platform_binding_policy: str | None = None


class PlatformBinding(StrictSchema):
    """Config-owned binding from a platform context to a durable agent."""

    adapter_id: str
    platform: str
    account_id: str | None = None
    context: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class AgentBindings(StrictSchema):
    """Inbound bindings declared for an agent."""

    native_default: bool = False
    platforms: tuple[PlatformBinding, ...] = ()


class AgentResources(StrictSchema):
    """Resource scopes used to build an agent-scoped runtime view."""

    memory_scopes: tuple[str, ...] = ("global", "workspace", "agent")
    skill_scopes: tuple[str, ...] = ("builtin", "global", "workspace", "agent")
    mcp_scopes: tuple[str, ...] = ("global", "agent")
    hook_profile: str | None = None
    model_profile: str | None = None
    prompt_profile: str | None = None


class AgentDefinition(StrictSchema):
    """ConfigManager-owned durable agent declaration."""

    id: str
    name: str
    role: AgentRole
    workspace: str
    state_dir: str
    runtime: AgentRuntimeDeclaration = Field(
        default_factory=AgentRuntimeDeclaration
    )
    policy: AgentPolicy = Field(default_factory=AgentPolicy)
    bindings: AgentBindings = Field(default_factory=AgentBindings)
    resources: AgentResources = Field(default_factory=AgentResources)
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def session_store_path(self) -> str:
        """Default per-agent SQLite path derived from state_dir."""

        return str(Path(self.state_dir) / "sessions" / "sessions.db")


class AgentRuntimeRecord(StrictSchema):
    """Manager/Supervisor-owned live state for a durable agent."""

    agent_id: str
    runtime_kind: AgentRuntimeKind
    process_id: int | None = None
    websocket_endpoint: str | None = None
    status: AgentStatus = AgentStatus.idle
    heartbeat_at: str | None = None
    started_at: str | None = None
    restart_count: int = 0
    queue_depth: int = 0
    active_turn_id: str | None = None
    last_exit_code: int | None = None
    last_error: str | None = None


class CallerIdentity(StrictSchema):
    """Authenticated caller metadata carried on Router/Manager contracts."""

    kind: CallerIdentityKind
    subject_id: str
    connection_id: str | None = None
    agent_id: str | None = None
    platform: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class RegistrationToken(StrictSchema):
    """Supervisor-issued token used only for Agent Runtime registration."""

    token_id: str
    secret: str
    issued_to_agent_id: str


class AgentRegistrationRequest(StrictSchema):
    """Agent Runtime -> Agent Hub.Manager registration contract."""

    agent_id: str
    runtime_kind: AgentRuntimeKind
    websocket_endpoint: str
    capabilities: tuple[str, ...] = ()
    heartbeat_interval_seconds: float = 5.0
    registration_token: RegistrationToken


class ManagementCall(StrictSchema):
    """Management-capable durable Agent -> Agent Hub.Manager contract."""

    operation: str
    caller: CallerIdentity
    capability: ManagementCapability
    target_agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RouterTarget(StrictSchema):
    """Router target hint; final truth comes from routing snapshot."""

    agent_id: str | None = None
    route_key: str | None = None


class RouterFrame(StrictSchema):
    """Access Agent / Agent Runtime -> Agent Hub.Router frame."""

    frame_id: str
    kind: RouterFrameKind
    source: str
    target: RouterTarget
    caller: CallerIdentity
    conversation_id: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    reply_sink: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class BindingPlanEntry(StrictSchema):
    """Manager materialized binding instruction for Access Agent."""

    adapter_id: str
    platform: str
    account_id: str | None = None
    target_agent_id: str
    enabled: bool = True
    context: dict[str, str] = Field(default_factory=dict)


class BindingPlan(StrictSchema):
    """Manager -> Access Agent binding plan."""

    revision: int
    entries: tuple[BindingPlanEntry, ...] = ()


class RoutingSnapshotEntry(StrictSchema):
    """Manager -> Router read-only route entry."""

    agent_id: str
    native_default: bool = False
    platform_bindings: tuple[PlatformBinding, ...] = ()


class RoutingSnapshot(StrictSchema):
    """Read-only routing table published to Router."""

    revision: int
    entries: tuple[RoutingSnapshotEntry, ...] = ()


def agent_state_dir(home: str | Path, agent_id: str) -> str:
    """Return the default state directory for a durable agent."""

    return str(Path(home).expanduser() / DEFAULT_AGENT_ROOT / agent_id)


def default_primary_agent_definition(home: str | Path, workspace: str) -> AgentDefinition:
    """Seed declaration for the default Mustang Agent instance."""

    state_dir = agent_state_dir(home, PRIMARY_AGENT_ID)
    return AgentDefinition(
        id=PRIMARY_AGENT_ID,
        name="Mustang Agent",
        role=AgentRole.PRIMARY,
        workspace=str(Path(workspace).expanduser()),
        state_dir=state_dir,
        runtime=AgentRuntimeDeclaration(
            kind=AgentRuntimeKind.in_process_session_agent
        ),
        policy=AgentPolicy(
            management_capabilities=(
                ManagementCapability.AGENT_CREATE,
                ManagementCapability.AGENT_DELETE,
                ManagementCapability.AGENT_STATUS,
                ManagementCapability.AGENT_CONTROL,
                ManagementCapability.GLOBAL_RESOURCE_WRITE,
            )
        ),
        bindings=AgentBindings(native_default=True),
        metadata={"seed": "default_primary"},
    )
