"""In-memory state for an active session and its turn queue.

A ``Session`` is the unit of conversational continuity: one orchestrator,
one event log, an FIFO of turns, and the client connections currently
observing it.  Every field is owned by ``SessionManager`` — nothing else
should mutate them directly.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kernel.agents.mustang.orchestrator.types import PermissionCallback
from kernel.core.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.core.protocol.interfaces.contracts.prompt_result import PromptResult

if TYPE_CHECKING:
    from kernel.agents.mustang.orchestrator import Orchestrator
    from kernel.core.protocol.interfaces.client_sender import ClientSender


@dataclass
class TurnState:
    """The prompt turn currently being executed."""

    request_id: str | int | None
    client_turn_id: str | None
    task: asyncio.Task[Any]
    started_at: datetime
    user_message_event_id: str
    completion_future: asyncio.Future[PromptResult]


@dataclass
class QueuedTurn:
    """A prompt turn waiting in the session FIFO queue."""

    request_id: str | int | None
    client_turn_id: str | None
    params: PromptParams
    queued_at: datetime
    response_future: asyncio.Future[PromptResult]
    text_collector: asyncio.Future[str] | None = None
    on_permission: PermissionCallback | None = None


@dataclass
class Session:
    """In-memory state for one active session.

    Idle sessions (no senders, no in-flight turn, empty queue) are
    evicted by ``SessionManager._maybe_evict``; the on-disk event log
    persists so a future ``session/load`` can rebuild the same state.
    """

    session_id: str
    cwd: Path
    created_at: datetime
    updated_at: datetime
    title: str | None
    git_branch: str | None
    mode_id: str | None
    config_options: dict[str, str]
    mcp_servers: list[dict[str, Any]]
    orchestrator: Orchestrator

    # Connections currently observing this session, keyed by connection id.
    senders: dict[str, ClientSender] = field(default_factory=dict)

    in_flight_turn: TurnState | None = None
    queue: deque[QueuedTurn] = field(default_factory=deque)
    last_event_id: str | None = None

    # Plan-mode bookkeeping mirrored from the orchestrator: ``pre_plan_mode``
    # remembers what to restore when leaving plan mode; the two flags drive
    # the post-plan reminder injected into the next turn.
    pre_plan_mode: str | None = None
    has_exited_plan_mode: bool = False
    needs_plan_mode_exit_attachment: bool = False

    # Mode changes captured by the sync ``_set_mode`` closure, drained
    # into ``ModeChangedEvent`` rows at the start of the next turn.
    pending_mode_changes: list[tuple[str | None, str]] = field(default_factory=list)

    # ``<system-reminder>`` blocks queued by hooks / cross-session messages,
    # popped by the orchestrator at the start of the next turn.
    pending_reminders: list[str] = field(default_factory=list)

    task_registry: Any = field(default=None)
    subagent_depth: int = 0
    user_executions: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
