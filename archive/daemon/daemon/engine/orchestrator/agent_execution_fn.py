"""Sub-agent execution function (Phase 5.2).

Standalone function extracted from the old ``_AgentExecutionMixin``.
Intercepts ``agent`` tool calls and runs a child orchestrator,
forwarding its stream events to the parent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, AsyncIterator

from daemon.engine.stream import (
    AgentEnd,
    AgentStart,
    PermissionRequest,
    StreamEvent,
    TextDelta,
    ToolCallResult,
    ToolCallStart,
)
from daemon.extensions.hooks.base import HookContext, HookEvent
from daemon.extensions.hooks.runner import run_hooks
from daemon.permissions.modes import PermissionMode
from daemon.providers.base import ToolUseContent

if TYPE_CHECKING:
    from daemon.engine.conversation import Conversation
    from daemon.engine.orchestrator.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

# Permission callback type (duplicated here to avoid circular imports).
PermissionCallback = Callable[[PermissionRequest], Any]


async def execute_agent_call(
    *,
    tc: ToolUseContent,
    permission_callback: PermissionCallback | None,
    agent_factory: Any,
    conversation: Conversation,
    tool_executor: ToolExecutor,
    on_entry: Callable[[Any], None] | None,
) -> AsyncIterator[StreamEvent]:
    """Run a sub-agent and forward its events.

    Args:
        tc: The ``agent`` tool call from the LLM.
        permission_callback: Shared approval callback.
        agent_factory: For spawning the child orchestrator.
        conversation: Parent's conversation (for last_assistant_text fallback).
        tool_executor: For emitting tool results.
        on_entry: Transcript writer.

    Yields:
        ``AgentStart``, forwarded child events, ``ToolCallResult``,
        ``AgentEnd``.
    """
    from daemon.extensions.tools.builtin.agent_tool import AgentTool

    # Validate input.
    try:
        params = AgentTool.Input.model_validate(tc.arguments)
    except Exception as exc:
        yield await tool_executor._emit_tool_result(
            tc,
            f"Invalid agent parameters: {exc}",
            conversation,
            on_entry,
            is_error=True,
        )
        return

    # Check factory availability.
    if agent_factory is None or not agent_factory.can_spawn:
        yield await tool_executor._emit_tool_result(
            tc,
            "Cannot spawn sub-agent: max depth reached or agent system not initialized.",
            conversation,
            on_entry,
            is_error=True,
        )
        return

    agent_id = uuid.uuid4().hex[:12]
    yield AgentStart(
        agent_id=agent_id,
        prompt=params.prompt,
        description=params.description,
    )

    # Fire subagent_start hook.
    try:
        hook_registry = tool_executor._hook_registry
        hooks = hook_registry.get_hooks(HookEvent.SUBAGENT_START)
        if hooks:
            ctx = HookContext(
                agent_description=params.description,
                depth=agent_factory.current_depth if hasattr(agent_factory, "current_depth") else None,
            )
            await run_hooks(hooks, ctx)
    except Exception:
        logger.exception("Error running subagent_start hook")

    # Parse optional permission mode.
    child_mode: PermissionMode | None = None
    if params.permission_mode:
        try:
            child_mode = PermissionMode(params.permission_mode)
        except ValueError:
            yield await tool_executor._emit_tool_result(
                tc,
                f"Invalid permission_mode: {params.permission_mode!r}",
                conversation,
                on_entry,
                is_error=True,
            )
            yield AgentEnd(agent_id=agent_id)
            return

    # Build child orchestrator.
    child = agent_factory.build_child(
        tools=params.tools,
        permission_mode=child_mode,
    )

    # Run child query with timeout, forwarding events.
    final_text_parts: list[str] = []
    timeout = agent_factory.timeout_seconds
    child_query_gen = child.query(params.prompt, permission_callback)

    try:
        async with asyncio.timeout(timeout):
            async for event in child_query_gen:
                if isinstance(
                    event,
                    (TextDelta, ToolCallStart, ToolCallResult, PermissionRequest),
                ):
                    yield event
                if isinstance(event, TextDelta):
                    final_text_parts.append(event.content)

    except TimeoutError:
        # Explicitly close the child async generator to cancel in-flight
        # tool executions and release resources.
        await child_query_gen.aclose()
        yield await tool_executor._emit_tool_result(
            tc,
            f"<agent timed out after {timeout}s>",
            conversation,
            on_entry,
            is_error=True,
        )
        yield AgentEnd(agent_id=agent_id)
        return

    except asyncio.CancelledError:
        yield await tool_executor._emit_tool_result(
            tc,
            "<agent cancelled before completion>",
            conversation,
            on_entry,
            is_error=True,
        )
        yield AgentEnd(agent_id=agent_id)
        raise

    # Collect final text.
    final_text = "".join(final_text_parts)
    if not final_text:
        final_text = child.conversation.last_assistant_text or "<agent produced no output>"

    yield await tool_executor._emit_tool_result(tc, final_text, conversation, on_entry)
    yield AgentEnd(agent_id=agent_id)
