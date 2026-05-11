"""SendMessageTool — send messages to running/stopped agents or other sessions.

Design reference: ``docs/plans/pending/send-message.md``.
Claude Code equivalent: ``src/tools/SendMessageTool/SendMessageTool.ts``.

Three routing paths:

- **In-session agent** (name or task_id): queue message for a running
  agent, or resume a stopped agent with transcript.
- **Durable agent** (``agent:<id>``): route message through Agent Hub.Router.
- **Cross-session** (``session:<id>``): deliver message to another
  session via ``SessionManager.deliver_message``.
- **Broadcast** (``*``): reserved for Team/Swarm (not yet supported).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from kernel.agents.mustang.orchestrator.types import ToolKind
from kernel.core.protocol.interfaces.contracts.text_block import TextBlock
from kernel.agents.mustang.tasks.id import generate_task_id
from kernel.agents.mustang.tasks.output import TaskOutput
from kernel.agents.mustang.tasks.types import AgentTaskState, TaskStatus, TaskType
from kernel.agents.mustang.tools.context import ToolContext
from kernel.agents.mustang.tools.tool import Tool
from kernel.agents.mustang.tools.types import (
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
)

logger = logging.getLogger(__name__)

_SESSION_PREFIX = "session:"
_AGENT_PREFIX = "agent:"


class SendMessageTool(Tool[dict[str, Any], dict[str, Any]]):
    """Send a message to a running/stopped agent or another session."""

    name = "SendMessage"
    description_key = "tools/send_message"
    description = "Send a message to another agent or session."
    kind = ToolKind.execute
    is_concurrency_safe = True

    input_schema = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": (
                    "Recipient: agent name, agent task_id, "
                    '"session:<session-id>" for cross-session, '
                    'or "*" for broadcast (not yet supported).'
                ),
            },
            "message": {
                "type": "string",
                "description": "The message content to send.",
            },
            "summary": {
                "type": "string",
                "description": ("A 5-10 word summary shown as a preview in the UI (optional)."),
            },
        },
        "required": ["to", "message"],
    }

    def is_read_only_call(self, input: dict[str, Any], ctx: Any) -> bool:
        return isinstance(input.get("message"), str)

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        to: str = input["to"]
        message: str = input["message"]

        # Path C: broadcast — reserved for Team/Swarm.
        if to == "*":
            yield _result(False, "Broadcast (to='*') is not yet supported.")
            return

        # Path B: cross-session via ACP.
        if to.startswith(_SESSION_PREFIX):
            target_session_id = to[len(_SESSION_PREFIX) :]
            if not target_session_id:
                yield _result(False, "Empty session ID in 'to' field.")
                return
            if ctx.deliver_cross_session is None:
                yield _result(
                    False,
                    "Cross-session messaging is not available (SessionManager not wired).",
                )
                return
            ok = ctx.deliver_cross_session(target_session_id, message)
            if ok:
                yield _result(
                    True,
                    f"Message delivered to session {target_session_id}.",
                )
            else:
                yield _result(
                    False,
                    f"Session '{target_session_id}' not found or not active.",
                )
            return

        # Durable peer agent route.  This is distinct from AgentTool
        # children, which remain local task-registry entries below.
        if to.startswith(_AGENT_PREFIX):
            target_agent_id = to[len(_AGENT_PREFIX) :]
            if not target_agent_id:
                yield _result(False, "Empty agent ID in 'to' field.")
                return
            if ctx.route_agent_message is None:
                yield _result(
                    False,
                    "Durable-agent messaging is not available (Router not wired).",
                )
                return
            ok = ctx.route_agent_message(target_agent_id, message)
            if ok:
                yield _result(True, f"Message routed to agent {target_agent_id}.")
            else:
                yield _result(
                    False,
                    f"Agent '{target_agent_id}' not found or cannot receive routed messages.",
                )
            return

        # Path A: in-session agent (name or task_id).
        registry = ctx.tasks
        if registry is None:
            yield _result(False, "Task registry not available.")
            return

        task_id = registry.resolve_name(to)
        if task_id is None:
            # Try as raw task_id.
            if registry.get(to) is not None:
                task_id = to
            else:
                yield _result(
                    False,
                    f"Agent '{to}' not found.  No agent with that name or task ID exists.",
                )
                return

        task = registry.get(task_id)
        if not isinstance(task, AgentTaskState):
            yield _result(False, f"Task '{task_id}' is not an agent task.")
            return

        # Running agent → queue the message.
        if task.status == TaskStatus.running:
            registry.queue_message(task_id, message)
            yield _result(
                True,
                f"Message queued for delivery to '{to}' at its next tool round.",
            )
            return

        # Stopped agent → resume with transcript.
        if task.status.is_terminal:
            yield await self._resume_agent(to, task, message, ctx)
            return

        # Unexpected status (pending?).
        yield _result(
            False,
            f"Agent '{to}' is in status '{task.status.value}' and cannot receive messages.",
        )

    async def _resume_agent(
        self,
        name_or_id: str,
        old_task: AgentTaskState,
        message: str,
        ctx: ToolContext,
    ) -> ToolCallResult:
        """Resume a stopped agent with its transcript and a new message."""
        if ctx.spawn_subagent is None:
            return _result_val(
                False,
                "Cannot resume agent: sub-agent spawning not available.",
            )

        registry = ctx.tasks
        if registry is None:
            return _result_val(False, "Task registry not available.")

        # Create a new task for the resumed agent.
        new_task_id = generate_task_id(TaskType.local_agent)
        output = TaskOutput(ctx.session_id, new_task_id)
        output_path = await output.init_file()

        new_task = AgentTaskState(
            id=new_task_id,
            status=TaskStatus.running,
            description=f"(resumed) {old_task.description}",
            owner_agent_id=ctx.agent_id,
            output_file=output_path,
            agent_id=new_task_id,
            agent_type=old_task.agent_type,
            prompt=message,
            model=old_task.model,
            name=old_task.name,
            is_backgrounded=True,
        )
        registry.register(new_task)

        # Update name mapping to point to the new task.
        if old_task.name:
            registry.update_name(old_task.name, new_task_id)

        # Launch the background agent with prior transcript.
        from kernel.agents.mustang.tools.builtin.agent import _run_agent_background

        spawn_fn = ctx.spawn_subagent

        asyncio.create_task(
            _run_agent_background(
                new_task_id,
                message,
                spawn_fn=spawn_fn,
                registry=registry,
                initial_history=old_task.transcript,
            )
        )

        label = f"'{old_task.name}'" if old_task.name else f"'{name_or_id}'"
        body = (
            f"Agent {label} was stopped ({old_task.status.value}); "
            f"resumed in background with your message.  "
            f"New task ID: {new_task_id}."
        )
        return _result_val(True, body)


def _result(success: bool, message: str) -> ToolCallResult:
    """Build a ToolCallResult for SendMessage responses."""
    return ToolCallResult(
        data={"success": success, "message": message},
        llm_content=[TextBlock(type="text", text=message)],
        display=TextDisplay(text=message),
    )


def _result_val(success: bool, message: str) -> ToolCallResult:
    """Same as _result but not wrapped in a generator yield."""
    return _result(success, message)


__all__ = ["SendMessageTool"]
