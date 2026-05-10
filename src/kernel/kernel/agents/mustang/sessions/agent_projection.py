"""Project per-agent session queues into Agent Control Plane status."""

from __future__ import annotations

from collections.abc import Iterable

from kernel.agent_hub.contracts import (
    AgentIdentity,
    AgentQueueState,
    AgentRuntimeKind,
    AgentStatus,
    AgentTaskIdentity,
    AgentControlOperation,
    StatusSnapshot,
)
from kernel.agents.mustang.sessions.runtime.state import QueuedTurn, Session


def project_session_agent_status(
    *,
    agent_id: str,
    sessions: Iterable[Session],
    runtime_kind: AgentRuntimeKind = AgentRuntimeKind.in_process_session_agent,
) -> StatusSnapshot:
    """Return a status view derived from SessionManager-owned state.

    This is a projection only.  The authoritative FIFO remains
    ``Session.queue`` and the active task remains ``Session.in_flight_turn``.
    """

    active_task_id: str | None = None
    queued_task_ids: list[str] = []

    for session in sessions:
        if session.in_flight_turn is not None and active_task_id is None:
            active_task_id = _active_task_id(session)
        queued_task_ids.extend(_queued_task_id(session, queued) for queued in session.queue)

    if active_task_id is not None:
        status = AgentStatus.running
        queue_state = AgentQueueState.draining if queued_task_ids else AgentQueueState.empty
    elif queued_task_ids:
        status = AgentStatus.queued
        queue_state = AgentQueueState.queued
    else:
        status = AgentStatus.idle
        queue_state = AgentQueueState.empty

    return StatusSnapshot(
        identity=AgentIdentity(agent_id=agent_id, runtime_kind=runtime_kind),
        status=status,
        queue_state=queue_state,
        active_task_id=active_task_id,
        queued_task_ids=tuple(queued_task_ids),
        metadata={"queue_depth": len(queued_task_ids)},
    )


def _active_task_id(session: Session) -> str:
    turn = session.in_flight_turn
    assert turn is not None
    request = turn.client_turn_id or turn.request_id or turn.user_message_event_id
    return f"{session.session_id}:{request}"


def _queued_task_id(session: Session, queued: QueuedTurn) -> str:
    request = queued.client_turn_id or queued.request_id or queued.queued_at.isoformat()
    return f"{session.session_id}:{request}"


def snapshot_to_task_identity(snapshot: StatusSnapshot) -> AgentTaskIdentity | None:
    """Return the active control task identity for a snapshot, if any."""

    if snapshot.active_task_id is None:
        return None
    return AgentTaskIdentity(
        task_id=snapshot.active_task_id,
        agent_id=snapshot.identity.agent_id,
        operation=AgentControlOperation.prompt,
    )


__all__ = ["project_session_agent_status", "snapshot_to_task_identity"]
