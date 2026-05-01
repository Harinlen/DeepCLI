"""The seven-step pipeline for a single tool call."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, Literal

import orjson

from kernel.hooks import HookEvent
from kernel.llm.types import ToolResultContent, ToolUseContent
from kernel.orchestrator.events import ToolCallProgress as ToolCallProgressEvent
from kernel.orchestrator.events import ToolCallResult as ToolCallResultEvent
from kernel.orchestrator.events import ToolCallStart
from kernel.orchestrator.permissions import PermissionCallback
from kernel.orchestrator.tools_exec.result_mapping import apply_result_budget, coerce_content
from kernel.orchestrator.tools_exec.shared import FILE_MUTATING_TOOLS, EventPair

if TYPE_CHECKING:
    from collections.abc import Callable

    from kernel.hooks import HookEventCtx
    from kernel.orchestrator.events import ToolCallError
    from kernel.tool_authz import AuthorizeContext, ToolAuthorizer
    from kernel.tool_authz.types import PermissionDecision
    from kernel.tools import Tool
    from kernel.tools.context import ToolContext

logger = logging.getLogger(__name__)


class ToolPipelineMixin:
    """Run validation, authorization, hooks, tool.call, and result mapping."""

    if TYPE_CHECKING:
        _on_context_changed: Callable[[ToolContext], None] | None

        def _error_tuple(
            self,
            tc: ToolUseContent,
            message: str,
        ) -> tuple[ToolCallError, ToolResultContent]:
            """Build matching client/LLM error results.

            Args:
                tc: Tool-use block that failed.
                message: Error message to surface.

            Returns:
                Tool error event and matching LLM tool result.
            """
            ...

        async def _authorize(
            self,
            *,
            authorizer: ToolAuthorizer | None,
            tool: Tool,
            tool_input: dict[str, Any],
            auth_ctx: AuthorizeContext,
            tc: ToolUseContent,
            on_permission: PermissionCallback,
        ) -> PermissionDecision | None:
            """Authorize a tool call.

            Args:
                authorizer: Optional ToolAuthorizer subsystem.
                tool: Tool being executed.
                tool_input: Effective tool input.
                auth_ctx: Authorization context.
                tc: Original tool-use block.
                on_permission: Interactive permission callback.

            Returns:
                Permission decision or ``None`` on fail-closed errors.
            """
            ...

        async def _fire_hook(
            self,
            *,
            event: HookEvent,
            mode: Literal["default", "plan", "bypass"],
            tool_name: str | None = None,
            tool_input: dict[str, Any] | None = None,
            tool_output: str | None = None,
            error_message: str | None = None,
        ) -> tuple[bool, HookEventCtx]:
            """Fire a tool lifecycle hook.

            Args:
                event: Hook event to fire.
                mode: Current projected permission mode.
                tool_name: Optional tool name.
                tool_input: Optional effective tool input.
                tool_output: Optional text output.
                error_message: Optional failure message.

            Returns:
                ``(blocked, hook_context)``.
            """
            ...

        async def _notify_file_touched(
            self,
            tool_name: str,
            tool_input: dict[str, Any],
        ) -> None:
            """Notify downstream subsystems that a file-mutating tool ran.

            Args:
                tool_name: Name of the mutating tool.
                tool_input: Effective tool input.

            Returns:
                ``None``.
            """
            ...

    async def _run_one(
        self,
        *,
        tc: ToolUseContent,
        tool: Tool,
        tool_ctx: ToolContext,
        auth_ctx: AuthorizeContext,
        authorizer: ToolAuthorizer | None,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
    ) -> AsyncGenerator[EventPair, None]:
        """Run validation, authorization, hooks, call, and result mapping.

        Args:
            tc: Original LLM tool-use block.
            tool: Resolved Tool implementation.
            tool_ctx: ToolContext passed to ``tool.call``.
            auth_ctx: Authorization context passed to ToolAuthorizer.
            authorizer: Optional ToolAuthorizer subsystem.
            on_permission: Interactive permission callback.
            mode: Current projected permission mode.

        Yields:
            Tool lifecycle event plus optional LLM-facing tool result.

        Raises:
            asyncio.CancelledError: Propagated so query cancellation can stop
                in-flight tool execution promptly.
        """
        try:
            await tool.validate_input(tc.input, tool_ctx)
        except Exception as exc:
            yield self._error_tuple(tc, f"invalid input: {exc}")
            return

        decision = await self._authorize(
            authorizer=authorizer,
            tool=tool,
            tool_input=tc.input,
            auth_ctx=auth_ctx,
            tc=tc,
            on_permission=on_permission,
        )
        if decision is None:
            yield self._error_tuple(tc, "permission check failed")
            return

        from kernel.tool_authz import PermissionAllow

        if not isinstance(decision, PermissionAllow):
            yield self._error_tuple(tc, getattr(decision, "message", "tool call denied"))
            return

        effective_input = decision.updated_input or tc.input
        blocked, pre_ctx = await self._fire_hook(
            event=HookEvent.PRE_TOOL_USE,
            mode=mode,
            tool_name=tool.name,
            tool_input=dict(effective_input),
        )
        if blocked:
            yield self._error_tuple(tc, "pre_tool_use hook blocked execution")
            return
        effective_input = pre_ctx.tool_input or effective_input

        raw_input_json: str | None = None
        try:
            raw_input_json = orjson.dumps(effective_input).decode()[:2000]
        except (TypeError, ValueError):
            raw_input_json = None
        yield (
            ToolCallStart(
                id=tc.id,
                title=tool.user_facing_name(effective_input),
                kind=tool.kind,
                raw_input=raw_input_json,
            ),
            None,
        )

        final_result = None
        try:
            async for event in tool.call(effective_input, tool_ctx):
                from kernel.tools.types import ToolCallProgress as TP
                from kernel.tools.types import ToolCallResult as TR

                if isinstance(event, TR):
                    final_result = event
                elif isinstance(event, TP):
                    if event.passthrough_event is not None:
                        yield (event.passthrough_event, None)
                    else:
                        yield (ToolCallProgressEvent(id=tc.id, content=list(event.content)), None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("tool %s execution failed", tool.name)
            await self._fire_hook(
                event=HookEvent.POST_TOOL_FAILURE,
                mode=mode,
                tool_name=tool.name,
                tool_input=dict(effective_input),
                error_message=str(exc),
            )
            yield self._error_tuple(tc, f"tool execution failed: {exc}")
            return

        if final_result is None:
            yield self._error_tuple(tc, "tool produced no result")
            return

        coerced = coerce_content(final_result.llm_content)
        await self._fire_hook(
            event=HookEvent.POST_TOOL_USE,
            mode=mode,
            tool_name=tool.name,
            tool_input=dict(effective_input),
            tool_output=coerced if isinstance(coerced, str) else None,
        )

        if final_result.context_modifier is not None:
            try:
                new_ctx = final_result.context_modifier(tool_ctx)
                if self._on_context_changed is not None:
                    self._on_context_changed(new_ctx)
            except Exception:
                logger.exception("context_modifier for %s failed", tool.name)

        if tool.name in FILE_MUTATING_TOOLS:
            await self._notify_file_touched(tool.name, effective_input)

        coerced = apply_result_budget(coerced, tool.max_result_size_chars)
        yield (
            ToolCallResultEvent(id=tc.id, content=list(final_result.llm_content)),
            ToolResultContent(tool_use_id=tc.id, content=coerced, is_error=False),
        )
