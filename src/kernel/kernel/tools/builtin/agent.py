"""AgentTool — spawn sub-agents for complex tasks.

Design reference: ``docs/plans/task-manager.md`` § 3.
Claude Code equivalent: ``src/tools/AgentTool/AgentTool.tsx``.

Two modes:
- **Foreground** (default): blocks until the sub-agent completes,
  transparently forwarding its events via ``passthrough_event``.
- **Background** (``run_in_background=True``): registers an
  ``AgentTaskState``, returns ``task_id`` immediately.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any

from kernel.orchestrator.events import TextDelta
from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tasks.id import generate_task_id
from kernel.tasks.output import TaskOutput
from kernel.tasks.registry import TaskRegistry
from kernel.tasks.types import AgentTaskState, TaskStatus, TaskType
from kernel.tools.context import ToolContext
from kernel.tools.tool import Tool
from kernel.tools.types import (
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
)

logger = logging.getLogger(__name__)


class AgentTool(Tool[dict[str, Any], str]):
    """Launch a new agent to handle complex, multi-step tasks."""

    name = "Agent"
    description_key = "tools/agent"
    description = "Launch a subagent to handle complex, multi-step tasks."
    kind = ToolKind.orchestrate

    input_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "A short (3-5 word) description of the task.",
            },
            "prompt": {
                "type": "string",
                "description": "The task for the agent to perform.",
            },
            "subagent_type": {
                "type": "string",
                "description": "The type of specialized agent to use.",
            },
            "name": {
                "type": "string",
                "description": (
                    "Optional name for the agent.  Background agents with a "
                    "name can be addressed via SendMessage.  Must be unique "
                    "within the session."
                ),
            },
            "model": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
                "description": "Optional model override for this agent.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this agent in the background.",
            },
        },
        "required": ["description", "prompt"],
    }

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        prompt = input["prompt"]
        run_in_background = bool(input.get("run_in_background", False))

        if run_in_background and ctx.tasks is not None:
            yield await self._spawn_background(input, ctx)
            return

        # -- Foreground synchronous mode --
        if ctx.spawn_subagent is None:
            yield _error("Sub-agent spawning not available at this depth")
            return

        result_text_parts: list[str] = []
        async for event in ctx.spawn_subagent(prompt, []):
            yield ToolCallProgress(
                content=[],
                passthrough_event=event,
            )
            if isinstance(event, TextDelta):
                result_text_parts.append(event.content)

        final_text = "".join(result_text_parts) or "(agent produced no output)"
        yield ToolCallResult(
            data={"result": final_text},
            llm_content=[TextBlock(type="text", text=final_text)],
            display=TextDisplay(text=final_text),
        )

    async def _spawn_background(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolCallResult:
        """Register an AgentTaskState and run the sub-agent in the background."""
        description = input["description"]
        prompt = input["prompt"]
        agent_type = input.get("subagent_type", "general-purpose")
        model = input.get("model")
        name: str | None = input.get("name")

        task_id = generate_task_id(TaskType.local_agent)

        # Register name if provided — fail early on duplicates.
        registry = ctx.tasks
        if name and registry is not None:
            if not registry.register_name(name, task_id):
                return _error(f"Agent name '{name}' is already taken. Choose a different name.")

        output = TaskOutput(ctx.session_id, task_id)
        output_path = await output.init_file()

        task = AgentTaskState(
            id=task_id,
            status=TaskStatus.running,
            description=description,
            owner_agent_id=ctx.agent_id,
            output_file=output_path,
            agent_id=task_id,
            agent_type=agent_type,
            prompt=prompt,
            model=model,
            name=name,
            is_backgrounded=True,
        )
        registry.register(task)  # type: ignore[union-attr]

        # Extract closures — don't capture the per-tool-call ctx.
        spawn_fn = ctx.spawn_subagent

        asyncio.create_task(
            _run_agent_background(
                task_id,
                prompt,
                spawn_fn=spawn_fn,
                registry=registry,  # type: ignore[arg-type]
            )
        )

        name_info = f" (name: '{name}')" if name else ""
        body = (
            f"Agent running in background with ID: {task_id}{name_info}. "
            f"You will be notified when it completes."
        )
        return ToolCallResult(
            data={"task_id": task_id, "status": "running", "name": name},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


async def _run_agent_background(
    task_id: str,
    prompt: str,
    *,
    spawn_fn: Callable[..., Any] | None,
    registry: TaskRegistry,
    initial_history: list[Any] | None = None,
) -> None:
    """Run a sub-agent in the background until completion.

    Does NOT capture ``ToolContext`` — only the ``spawn_fn`` closure
    (Orchestrator-level lifetime) and ``registry`` (session-level).

    When *initial_history* is provided (agent resume), the sub-agent
    starts with prior conversation context.
    """
    if spawn_fn is None:
        registry.update_status(task_id, TaskStatus.failed, error="spawn_subagent not available")
        registry.enqueue_notification(task_id)
        return

    try:
        result_parts: list[str] = []
        transcript_holder: list[Any] = []

        async for event in spawn_fn(
            prompt,
            [],
            agent_id=task_id,
            initial_history=initial_history,
        ):
            if isinstance(event, TextDelta):
                result_parts.append(event.content)
            # Capture transcript from SubAgentEnd event if available.
            if hasattr(event, "transcript") and event.transcript is not None:
                transcript_holder = event.transcript

        result = "".join(result_parts) or "(agent produced no output)"
        registry.update_status(task_id, TaskStatus.completed, result=result)

        # Store transcript for potential resume via SendMessage.
        task = registry.get(task_id)
        if isinstance(task, AgentTaskState) and transcript_holder:
            task.transcript = transcript_holder

    except asyncio.CancelledError:
        registry.update_status(task_id, TaskStatus.killed)
    except Exception as exc:
        logger.exception("Background agent %s failed", task_id)
        registry.update_status(task_id, TaskStatus.failed, error=str(exc))

    registry.enqueue_notification(task_id)


def _error(msg: str) -> ToolCallResult:
    return ToolCallResult(
        data={"error": msg},
        llm_content=[TextBlock(type="text", text=f"Error: {msg}")],
        display=TextDisplay(text=f"Error: {msg}"),
    )


__all__ = ["AgentTool"]
