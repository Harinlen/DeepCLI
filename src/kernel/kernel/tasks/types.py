"""Task data models — shared by all task types.

Design reference: ``docs/plans/task-manager.md`` § 1.1.
Claude Code equivalent: ``src/Task.ts``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kernel.orchestrator.history import Message


class TaskType(str, enum.Enum):
    """All supported background task types."""

    local_bash = "local_bash"
    local_agent = "local_agent"
    monitor = "monitor"


class TaskStatus(str, enum.Enum):
    """Unified lifecycle states for all task types."""

    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    killed = "killed"

    @property
    def is_terminal(self) -> bool:
        """True when the task will not transition further."""
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset({TaskStatus.completed, TaskStatus.failed, TaskStatus.killed})


@dataclass
class TaskStateBase:
    """Fields shared by all task types.

    Mutable — ``status``, ``end_time``, ``notified`` etc. are updated
    in place by :class:`~kernel.tasks.registry.TaskRegistry`.
    """

    id: str
    type: TaskType
    status: TaskStatus
    description: str
    tool_use_id: str | None = None
    """The ``tool_use_id`` from the LLM response that spawned this task."""

    owner_agent_id: str | None = None
    """Agent that owns this task (``None`` = root agent).  Used for
    notification routing — each Orchestrator only drains its own."""

    start_time: float = 0.0
    end_time: float | None = None
    output_file: str = ""
    """Path to the on-disk output file (written by subprocess fd)."""

    output_offset: int = 0
    """Byte offset consumed so far (for incremental reads)."""

    notified: bool = False
    """Whether the completion notification has been pushed to the LLM."""


@dataclass
class ShellTaskState(TaskStateBase):
    """Background shell command state."""

    type: TaskType = field(default=TaskType.local_bash, init=False)
    command: str = ""
    exit_code: int | None = None

    process: Any = field(default=None, repr=False)
    """``asyncio.subprocess.Process`` while running, ``None`` after."""


@dataclass
class AgentTaskState(TaskStateBase):
    """Background sub-agent state."""

    type: TaskType = field(default=TaskType.local_agent, init=False)
    agent_id: str = ""
    agent_type: str = ""
    """e.g. ``"Explore"``, ``"general-purpose"``."""

    prompt: str = ""
    model: str | None = None
    result: str | None = None
    """Final text reply from the sub-agent."""

    error: str | None = None
    progress: AgentProgress | None = None
    is_backgrounded: bool = False
    """``False`` = foreground synchronous, ``True`` = backgrounded."""

    name: str | None = None
    """Optional human-readable name assigned via ``AgentTool(name=...)``.
    Used by ``SendMessageTool`` to address this agent by name."""

    pending_messages: list[str] = field(default_factory=list)
    """Messages queued via SendMessage, drained at tool-round boundaries."""

    transcript: list[Message] | None = field(default=None, repr=False)
    """Conversation history captured when the agent completes.  Used by
    ``SendMessageTool`` to resume the agent with its prior context.
    In-memory only — not persisted to SQLite."""

    cancel_event: Any = field(default=None, repr=False)
    """``asyncio.Event`` — set to cancel the agent."""


@dataclass
class AgentProgress:
    """Sub-agent real-time progress snapshot."""

    tool_use_count: int = 0
    token_count: int = 0
    last_activity: str | None = None


@dataclass
class MonitorTaskState(TaskStateBase):
    """Long-running monitor task state.

    Design reference: ``docs/plans/schedule-manager.md`` § 4.4.

    Unlike ``ShellTaskState`` (fire-and-forget), a monitor task
    continuously buffers new output lines into ``recent_lines``.
    The Orchestrator drains this buffer every turn and injects
    the lines as a ``<monitor-update>`` system reminder.
    """

    type: TaskType = field(default=TaskType.monitor, init=False)
    command: str = ""
    exit_code: int | None = None

    process: Any = field(default=None, repr=False)
    """``asyncio.subprocess.Process`` while running, ``None`` after."""

    recent_lines: list[str] = field(default_factory=list)
    """Ring buffer of new stdout lines since the last drain."""

    max_buffered_lines: int = 50
    """Cap on ``recent_lines`` — oldest lines are evicted when full."""


TaskState = ShellTaskState | AgentTaskState | MonitorTaskState
"""Union type for runtime dispatch."""


__all__ = [
    "AgentProgress",
    "AgentTaskState",
    "MonitorTaskState",
    "ShellTaskState",
    "TaskState",
    "TaskStateBase",
    "TaskStatus",
    "TaskType",
]
