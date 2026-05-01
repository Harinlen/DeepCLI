"""Sub-agent bracketing events.

These events let the Session layer nest a child agent transcript inside the
parent stream without making the parent query loop understand child internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kernel.orchestrator.stop import StopReason


@dataclass(frozen=True)
class SubAgentStart:
    """Marks the beginning of a sub-agent event stream.

    Session persistence uses this as an opening delimiter; clients may render
    following events as belonging to the child until ``SubAgentEnd`` appears.
    """

    # Stable child identifier used by task notifications and transcript files.
    agent_id: str
    # Human-facing task description shown in UI task rows.
    description: str
    # Configured agent flavor; currently lightweight but preserved for routing.
    agent_type: str
    # Parent tool_use id, so ACP clients can correlate child output to a tool.
    spawned_by_tool_id: str


@dataclass(frozen=True)
class SubAgentEnd:
    """Marks the end of a sub-agent event stream.

    ``transcript`` is optional because background agents may outlive the parent
    stream; foreground agent calls attach it so tests and persistence can verify
    the exact child conversation.
    """

    # Must match the id from ``SubAgentStart``; event consumers treat it as a
    # delimiter pair rather than independent metadata.
    agent_id: str
    stop_reason: StopReason
    transcript: list[Any] | None = None
