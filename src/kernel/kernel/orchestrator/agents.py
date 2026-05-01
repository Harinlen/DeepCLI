"""Sub-agent spawning support for StandardOrchestrator."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from kernel.llm.types import TextContent
from kernel.orchestrator.constants import SUBAGENT_DEFAULT_MAX_TURNS
from kernel.orchestrator.events import SubAgentEnd, SubAgentStart
from kernel.orchestrator.runtime import SubAgentParentRuntime
from kernel.orchestrator.types import PermissionCallback, PermissionResponse, StopReason
from kernel.tasks.id import generate_task_id
from kernel.tasks.types import TaskType


def make_spawn_subagent(parent: SubAgentParentRuntime) -> Any:
    """Build the ToolContext ``spawn_subagent`` closure.

    Args:
        parent: Root or parent orchestrator runtime that owns shared deps,
            config, cwd, and permission behavior.

    Returns:
        Async generator function compatible with ``ToolContext.spawn_subagent``.

    Yields:
        The returned closure yields sub-agent bracketing and child events.
    """

    async def _auto_allow(_req: Any) -> PermissionResponse:
        """Allow child-agent tool calls when no interactive callback is supplied.

        Args:
            _req: Permission request ignored by the child fallback policy.

        Returns:
            One-shot allow response for the current tool call.
        """
        return PermissionResponse(decision="allow_once")

    async def spawn_subagent(
        prompt: str,
        attachments: list[Any],
        *,
        agent_id: str | None = None,
        on_permission: PermissionCallback | None = None,
        initial_history: list[Any] | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Run a child StandardOrchestrator and bracket its event stream.

        Args:
            prompt: Prompt text handed to the child agent.
            attachments: Reserved attachment payloads from the Agent tool.
            agent_id: Optional caller-supplied child id.
            on_permission: Optional child permission callback.
            initial_history: Optional restored child conversation history.

        Yields:
            ``SubAgentStart``, child events, and the matching ``SubAgentEnd``.
        """
        from kernel.orchestrator.orchestrator import StandardOrchestrator

        if agent_id is None:
            agent_id = generate_task_id(TaskType.local_agent)
        child = StandardOrchestrator(
            deps=parent._deps,
            session_id=f"{parent._session_id}/agent-{agent_id}",
            initial_history=initial_history or [],
            config=parent._config,
            depth=parent._depth + 1,
            cwd=parent._cwd,
            agent_id=agent_id,
        )
        yield SubAgentStart(
            agent_id=agent_id,
            description=prompt[:80],
            agent_type="general-purpose",
            spawned_by_tool_id="",
        )
        async for event in child.query(
            [TextContent(text=prompt)],
            on_permission=on_permission or _auto_allow,
            max_turns=SUBAGENT_DEFAULT_MAX_TURNS,
        ):
            yield event
        transcript = list(child._history.messages) if child._history.messages else None
        yield SubAgentEnd(
            agent_id=agent_id,
            stop_reason=child.stop_reason or StopReason.end_turn,
            transcript=transcript,
        )
        drain_orphan_notifications(parent, agent_id)

    return spawn_subagent


def drain_orphan_notifications(parent: SubAgentParentRuntime, ended_agent_id: str) -> None:
    """After a sub-agent ends, claim its remaining task notifications.

    Args:
        parent: Parent runtime whose reminder queue receives orphaned notices.
        ended_agent_id: Child id whose notifications should be drained.

    Returns:
        ``None``.
    """
    from kernel.orchestrator.notifications import format_task_notification

    registry = parent._deps.task_registry
    if registry is None:
        return
    for task_id in registry.drain_notifications(agent_id=ended_agent_id):
        task = registry.get(task_id)
        if task is not None and parent._deps.queue_reminders is not None:
            parent._deps.queue_reminders([format_task_notification(task)])
