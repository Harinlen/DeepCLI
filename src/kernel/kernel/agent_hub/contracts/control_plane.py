"""Shared vocabulary for Mustang's Agent Control Plane.

This module defines the southbound control interface used by future runtimes:
in-process Mustang Agents, child Kernel runtimes, and external ACP-compatible
agents. It intentionally contains no runtime implementation or app wiring.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from kernel.core.protocol.acp.namespaces import AcpMethod, MustangMethod


class AgentRuntimeKind(StrEnum):
    """Runtime families the control plane can target."""

    in_process_session_agent = "in_process_session_agent"
    child_kernel = "child_kernel"
    external_acp = "external_acp"


class AgentStatus(StrEnum):
    """Lifecycle state for a controlled agent runtime."""

    idle = "idle"
    running = "running"
    queued = "queued"
    canceling = "canceling"
    paused = "paused"
    closed = "closed"
    error = "error"

    @property
    def accepts_new_work(self) -> bool:
        """Whether a prompt/message can be submitted without unpausing."""
        return self in {AgentStatus.idle, AgentStatus.running, AgentStatus.queued}


class AgentQueueState(StrEnum):
    """Queue owner/drain state for a target agent."""

    empty = "empty"
    queued = "queued"
    draining = "draining"
    paused = "paused"


class AgentControlOperation(StrEnum):
    """First-class operations shared by northbound and southbound control."""

    create = "create"
    load = "load"
    resume = "resume"
    prompt = "prompt"
    send_message = "send_message"
    cancel = "cancel"
    pause = "pause"
    status = "status"
    close = "close"
    delete = "delete"


ACP_METHOD_BY_OPERATION: Mapping[AgentControlOperation, str] = {
    AgentControlOperation.create: AcpMethod.SESSION_NEW,
    AgentControlOperation.load: AcpMethod.SESSION_LOAD,
    AgentControlOperation.resume: AcpMethod.SESSION_RESUME,
    AgentControlOperation.prompt: AcpMethod.SESSION_PROMPT,
    AgentControlOperation.cancel: AcpMethod.SESSION_CANCEL,
    AgentControlOperation.close: AcpMethod.SESSION_CLOSE,
}
"""Operations with direct official ACP method equivalents."""


MUSTANG_METHOD_BY_OPERATION: Mapping[AgentControlOperation, str] = {
    AgentControlOperation.send_message: "_mustang.agent/agent/send_message",
    AgentControlOperation.pause: "_mustang.agent/agent/pause",
    AgentControlOperation.status: "_mustang.agent/agent/status",
    AgentControlOperation.delete: MustangMethod.SESSION_DELETE,
}
"""Operations that require Mustang extension semantics today."""


@dataclass(frozen=True)
class AgentIdentity:
    """Stable identity mapping for a controlled agent runtime."""

    agent_id: str
    runtime_kind: AgentRuntimeKind
    mustang_session_id: str | None = None
    acp_session_id: str | None = None
    provider_session_id: str | None = None
    acpx_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTaskIdentity:
    """Identity for one in-flight or queued control-plane operation."""

    task_id: str
    agent_id: str
    operation: AgentControlOperation
    acp_request_id: str | int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeTarget:
    """Address of a southbound runtime backend."""

    kind: AgentRuntimeKind
    name: str | None = None
    command: list[str] | None = None
    endpoint: str | None = None
    cwd: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StatusSnapshot:
    """Point-in-time state for an agent runtime."""

    identity: AgentIdentity
    status: AgentStatus
    queue_state: AgentQueueState = AgentQueueState.empty
    active_task_id: str | None = None
    queued_task_ids: tuple[str, ...] = ()
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ControlResult:
    """Generic result envelope for control-plane operations."""

    identity: AgentIdentity
    task: AgentTaskIdentity | None = None
    status: StatusSnapshot | None = None
    output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntimeController(Protocol):
    """Southbound interface implemented by each runtime backend."""

    async def create(self, target: RuntimeTarget, *, name: str | None = None) -> ControlResult:
        """Create a target agent/session record."""

    async def load(self, identity: AgentIdentity, *, replay: bool) -> ControlResult:
        """Load existing state, optionally replaying transcript updates."""

    async def resume(self, identity: AgentIdentity) -> ControlResult:
        """Reattach to existing state without replay."""

    async def prompt(self, identity: AgentIdentity, prompt: str) -> ControlResult:
        """Start or enqueue a user-prompt turn."""

    async def send_message(self, identity: AgentIdentity, message: str) -> ControlResult:
        """Deliver a non-prompt message to an existing agent."""

    async def cancel(self, identity: AgentIdentity, *, task_id: str | None = None) -> ControlResult:
        """Cooperatively cancel the active task or a specific task."""

    async def pause(self, identity: AgentIdentity) -> ControlResult:
        """Stop accepting or draining new queued work."""

    async def status(self, identity: AgentIdentity) -> StatusSnapshot:
        """Return current runtime and queue status."""

    async def close(self, identity: AgentIdentity) -> ControlResult:
        """Release active runtime resources while preserving durable state."""

    async def delete(self, identity: AgentIdentity, *, force: bool = False) -> ControlResult:
        """Delete Mustang-owned durable state."""
