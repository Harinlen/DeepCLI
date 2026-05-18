"""Pydantic schemas for Access Router runtime routing."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RuntimeRegisterRequest(BaseModel):
    """Agent Runtime route registration request."""

    process_id: str
    pid: int
    role: str = "agent_runtime"
    agent_id: str
    protocol_version: int
    capabilities: tuple[str, ...] = ()
    auth_token: str
    last_seen_event_id: str | None = None
    last_seen_revision: int | None = None


class RuntimeRegisterResult(BaseModel):
    """Runtime registration result."""

    agent_id: str
    connection_id: str
    status: str


class RuntimePing(BaseModel):
    """Runtime ping payload."""

    connection_id: str


class RuntimePong(BaseModel):
    """Runtime pong payload."""

    connection_id: str
    ok: bool = True


class DeliverTurnRequest(BaseModel):
    """Local client turn routed to an Agent Runtime."""

    agent_id: str = "primary"
    session_id: str
    client_turn_id: str
    prompt: str
    idempotency_key: str | None = None


class RuntimeAcpRequest(BaseModel):
    """ACP/JSON-RPC request routed to an Agent Runtime."""

    agent_id: str = "primary"
    method: str
    params: dict[str, object] = Field(default_factory=dict)
    session_id: str | None = None
    request_id: str | int | None = None
    idempotency_key: str | None = None


class CancelTurnRequest(BaseModel):
    """Cancel a client turn."""

    agent_id: str
    client_turn_id: str


class RuntimeEventEnvelope(BaseModel):
    """Runtime event envelope."""

    agent_id: str
    session_id: str
    event_type: str
    payload: dict[str, object] = Field(default_factory=dict)


class RouteStatus(BaseModel):
    """Current route status for one Agent."""

    agent_id: str
    status: str
    connection_id: str | None = None


class RegisteredAgent(BaseModel):
    """Registered runtime route projection."""

    agent_id: str
    connection_id: str
    pid: int
    protocol_version: int


class RouterHealth(BaseModel):
    """Access Router health projection."""

    ready: bool
    registered_agents: int
    agent_hub_forward_count: int = 0


class RoutingSnapshotStatus(BaseModel):
    """Routing snapshot reload result."""

    reloaded: bool
    revision: int | None = None


class AdapterStatus(BaseModel):
    """Access adapter status projection."""

    adapter_id: str
    status: str
    error: str | None = None
