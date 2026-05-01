"""Per-tool execution — permissions, hooks, side-effect dispatch.

Handles one tool call at a time: permission check (with optional
prompt round-trip), pre-tool-use hooks, the actual ``tool.execute()``
call, result budget enforcement, post-tool-use hooks, transcript
write-back, and finally the typed ``SideEffect`` dispatch.

Also owns the cancel-aware synthetic-result finalizer and parallel
execution grouping.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any, AsyncIterator, Literal

from daemon.engine.conversation import Conversation
from daemon.engine.stream import (
    PermissionRequest,
    PermissionResponse,
    StreamEvent,
    TaskUpdate,
    ToolCallResult,
)
from daemon.errors import ToolExecutionError
from daemon.extensions.hooks.base import HookContext, HookEvent
from daemon.extensions.hooks.registry import HookRegistry
from daemon.extensions.hooks.runner import run_hooks
from daemon.extensions.tools.base import ConcurrencyHint, ToolContext
from daemon.extensions.tools.registry import ToolRegistry
from daemon.extensions.tools.result_store import ResultStore
from daemon.permissions.engine import PermissionDecision, PermissionEngine
from daemon.permissions.modes import PermissionMode
from daemon.providers.base import ImageContent, ToolUseContent
from daemon.sessions.entry import ToolCallEntry
from daemon.sessions.image_cache import ImageCache
from daemon.side_effects import (
    EnterPlanMode,
    ExitPlanMode,
    FileChanged,
    SkillActivated,
    TasksUpdated,
)

from daemon.engine.orchestrator.concurrency import (
    ExecutionSlot,
    plan_execution_groups,
)

logger = logging.getLogger(__name__)

# Callback type for permission round-trip.
PermissionCallback = Callable[[PermissionRequest], asyncio.coroutines]

# Cancellation phases.
_CancelPhase = Literal["permission_wait", "pre_hooks", "executing"]

_CANCEL_MESSAGES: dict[str, str] = {
    "permission_wait": (
        "<cancelled: tool was not executed (interrupted before user approval completed)>"
    ),
    "pre_hooks": "<cancelled: tool was not executed (interrupted during pre_tool_use hooks)>",
    "executing": (
        "<cancelled mid-execution: the tool MAY have partially "
        "completed. Verify current state before retry.>"
    ),
}


class ToolExecutor:
    """Executes tool calls with permission checks, hooks, and side-effects.

    Args:
        permission_engine: Shared permission engine.
        tool_registry: Available tools.
        hook_registry: Event hooks.
        result_store: For persisting large tool outputs.
        image_cache: For tool-returned images.
        max_result_chars_override: Global override for tool output budget.
    """

    def __init__(
        self,
        permission_engine: PermissionEngine,
        tool_registry: ToolRegistry,
        hook_registry: HookRegistry | None = None,
        result_store: ResultStore | None = None,
        image_cache: ImageCache | None = None,
        max_result_chars_override: int | None = None,
        plan_mode_controller: Any = None,
        skill_setter: Callable[[str | None], None] | None = None,
        task_store: Any = None,
    ) -> None:
        self._permission_engine = permission_engine
        self._tool_registry = tool_registry
        self._hook_registry = hook_registry or HookRegistry()
        self._result_store = result_store
        self._image_cache = image_cache
        self._max_result_chars_override = max_result_chars_override
        self._plan_mode_controller = plan_mode_controller
        self._skill_setter = skill_setter
        self._task_store = task_store
        # Maps tool_call_id → (phase, ToolUseContent) for in-flight tools.
        self._in_flight_tools: dict[str, tuple[_CancelPhase, ToolUseContent]] = {}

    # -- Public properties -------------------------------------------------

    @property
    def tool_registry(self) -> ToolRegistry:
        """Current tool registry."""
        return self._tool_registry

    @property
    def permission_engine(self) -> PermissionEngine:
        """Shared permission engine."""
        return self._permission_engine

    # -- Emit helper -------------------------------------------------------

    async def _emit_tool_result(
        self,
        tc: ToolUseContent,
        output: str,
        conversation: Conversation,
        on_entry: Callable[[Any], None] | None,
        *,
        is_error: bool = False,
        image_parts: list[ImageContent] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolCallResult:
        """Build a ToolCallResult and record it in conversation + transcript."""
        persisted_parts = self._persist_image_parts(image_parts)

        await conversation.add_tool_result(
            tc.tool_call_id,
            output,
            is_error=is_error,
            image_parts=image_parts,
        )

        if on_entry:
            entry_parts = [p.model_dump() for p in persisted_parts] if persisted_parts else None
            on_entry(
                ToolCallEntry(
                    tool_call_id=tc.tool_call_id,
                    tool_name=tc.name,
                    arguments=tc.arguments,
                    output=output,
                    is_error=is_error,
                    image_parts=entry_parts,
                )
            )

        # Spread rendering metadata into the event for the CLI.
        extra: dict[str, Any] = {}
        if metadata:
            if "output_type" in metadata:
                extra["output_type"] = metadata["output_type"]
            if "file_path" in metadata:
                extra["file_path"] = metadata["file_path"]
            if "exit_code" in metadata:
                extra["exit_code"] = metadata["exit_code"]

        return ToolCallResult(
            tool_call_id=tc.tool_call_id,
            tool_name=tc.name,
            output=output,
            is_error=is_error,
            image_parts=persisted_parts,
            **extra,
        )

    def _persist_image_parts(
        self,
        image_parts: list[ImageContent] | None,
    ) -> list[ImageContent] | None:
        """Write image bytes to cache, return lightweight copies for JSONL."""
        if not image_parts or self._image_cache is None:
            return None
        stripped: list[ImageContent] = []
        for part in image_parts:
            self._image_cache.store(part)
            stripped.append(
                ImageContent(
                    media_type=part.media_type,
                    data_base64="",
                    source_sha256=part.source_sha256,
                    source_path=part.source_path,
                )
            )
        return stripped

    # -- Cancel-safe finalizer ---------------------------------------------

    async def finalize_cancelled_calls(
        self,
        conversation: Conversation,
        on_entry: Callable[[Any], None] | None,
    ) -> None:
        """Write synthetic tool_result for every in-flight tool.

        Invoked under ``asyncio.shield`` when a query is cancelled.
        """
        pending = dict(self._in_flight_tools)
        self._in_flight_tools.clear()
        if not pending:
            return

        for call_id, (phase, tc) in pending.items():
            message = _CANCEL_MESSAGES.get(phase, f"<cancelled in {phase}>")
            try:
                await conversation.add_tool_result(tc.tool_call_id, message, is_error=True)
            except Exception:
                logger.exception("Failed to add synthetic tool_result for %s", call_id)
                continue

            if on_entry is not None:
                try:
                    on_entry(
                        ToolCallEntry(
                            tool_call_id=tc.tool_call_id,
                            tool_name=tc.name,
                            arguments=tc.arguments,
                            output=message,
                            is_error=True,
                            synthetic=True,
                            cancel_phase=phase,
                        )
                    )
                except Exception:
                    logger.exception("Failed to persist synthetic cancel entry for %s", call_id)

            logger.info(
                "Finalized cancelled tool call %s (%s) in phase=%s",
                call_id,
                tc.name,
                phase,
            )

    # -- Single tool execution ---------------------------------------------

    async def execute(
        self,
        tc: ToolUseContent,
        ctx: ToolContext,
        conversation: Conversation,
        on_entry: Callable[[Any], None] | None,
        permission_callback: PermissionCallback | None,
        *,
        execute_agent_call: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        """Execute a single tool call, handling permissions.

        Args:
            tc: The tool call from the LLM.
            ctx: Tool execution context.
            conversation: Conversation to record results into.
            on_entry: Transcript writer callback.
            permission_callback: Async callback for permission prompts.
            execute_agent_call: Callback for agent tool interception.
                Signature: ``(tc, permission_callback) -> AsyncIterator[StreamEvent]``.

        Yields:
            PermissionRequest and/or ToolCallResult events.
        """
        tool = self._tool_registry.get(tc.name)
        if tool is None:
            yield await self._emit_tool_result(
                tc,
                f"Unknown tool: {tc.name}",
                conversation,
                on_entry,
                is_error=True,
            )
            return

        # Deferred-execution gate: tools that represent user decisions
        # (plan mode entry/exit, structured questions) always ask the
        # user via PermissionRequest before running.  Bypasses the
        # permission engine entirely — these aren't rule-matched, they
        # are explicit decisions.
        if tool.defer_execution:
            request_id = uuid.uuid4().hex[:12]
            perm_req = PermissionRequest(
                request_id=request_id,
                tool_name=tc.name,
                arguments=tc.arguments,
                suggested_rule=None,  # No persistable rule for decisions
                warning=None,
            )
            yield perm_req

            response: PermissionResponse | None = None
            if permission_callback is not None:
                self._in_flight_tools[tc.tool_call_id] = ("permission_wait", tc)
                try:
                    response = await permission_callback(perm_req)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Permission callback error (deferred tool)")
                self._in_flight_tools.pop(tc.tool_call_id, None)

            if response is None or response.decision == "deny":
                # Tell the model the user declined — let it react.
                # Not an error: a legitimate user choice.
                yield await self._emit_tool_result(
                    tc,
                    "User declined this action.",
                    conversation,
                    on_entry,
                    is_error=False,
                )
                return
            # Approved — fall through to normal execution path,
            # but skip the standard permission engine check below.

        # Permission check (skipped for defer_execution tools above).
        if tool.defer_execution:
            decision = PermissionDecision.ALLOW
        else:
            decision = self._permission_engine.check(tool, tc.arguments)

        if decision == PermissionDecision.DENY:
            if self._permission_engine.mode == PermissionMode.PLAN:
                msg = (
                    "Tool not available in plan mode. Only read-only tools "
                    "(file_read, glob, grep) and plan-file edits are allowed."
                )
            else:
                msg = "Tool execution denied by permission rule."
            await self._fire_permission_denied_hook(tc)
            yield await self._emit_tool_result(
                tc,
                msg,
                conversation,
                on_entry,
                is_error=True,
            )
            return

        if decision == PermissionDecision.PROMPT:
            suggested = self._permission_engine.generate_rule_for_tool(tc.name, tc.arguments)
            request_id = uuid.uuid4().hex[:12]

            # Attach destructive-command warning if the tool supports it.
            warning: str | None = None
            if hasattr(tool, "get_destructive_warning"):
                warning = tool.get_destructive_warning(tc.arguments)

            perm_req = PermissionRequest(
                request_id=request_id,
                tool_name=tc.name,
                arguments=tc.arguments,
                suggested_rule=suggested,
                warning=warning,
            )
            yield perm_req

            response: PermissionResponse | None = None
            if permission_callback is not None:
                self._in_flight_tools[tc.tool_call_id] = ("permission_wait", tc)
                try:
                    response = await permission_callback(perm_req)
                except asyncio.CancelledError:
                    # Leave in _in_flight_tools so finalize_cancelled_calls
                    # can synthesize a result for this orphaned tool call.
                    raise
                except Exception:
                    logger.exception("Permission callback error")
                self._in_flight_tools.pop(tc.tool_call_id, None)

            if response is None or response.decision == "deny":
                await self._fire_permission_denied_hook(tc)
                consecutive = self._permission_engine.record_denial(tc.name)
                hint = ""
                if consecutive >= 3 and suggested:
                    hint = (
                        f"\n\nHint: {tc.name} has been denied {consecutive} times. "
                        f"Consider 'Always Allow' to add a permanent rule "
                        f"({suggested})."
                    )
                yield await self._emit_tool_result(
                    tc,
                    f"Tool execution denied by user.{hint}",
                    conversation,
                    on_entry,
                    is_error=True,
                )
                return

            if response.decision == "always_allow":
                try:
                    self._permission_engine.settings.add_allow_rule(suggested)
                except ValueError:
                    logger.warning("Cannot add allow rule %r", suggested)
            self._permission_engine.record_allow(tc.name)

        # --- Agent tool interception ---
        from daemon.extensions.tools.builtin.agent_tool import AgentTool

        if isinstance(tool, AgentTool) and execute_agent_call is not None:
            async for evt in execute_agent_call(tc, permission_callback):
                yield evt
            return

        # --- pre_tool_use hooks ---
        pre_hooks = self._hook_registry.get_hooks(HookEvent.PRE_TOOL_USE, tc.name, tc.arguments)
        if pre_hooks:
            hook_ctx = HookContext(tool_name=tc.name, tool_input=tc.arguments)
            self._in_flight_tools[tc.tool_call_id] = ("pre_hooks", tc)
            try:
                hook_result = await run_hooks(pre_hooks, hook_ctx)
            except asyncio.CancelledError:
                raise
            self._in_flight_tools.pop(tc.tool_call_id, None)
            if hook_result.blocked:
                msg = hook_result.output or "Blocked by pre_tool_use hook"
                yield await self._emit_tool_result(
                    tc,
                    msg,
                    conversation,
                    on_entry,
                    is_error=True,
                )
                return

        # --- Execute ---
        self._in_flight_tools[tc.tool_call_id] = ("executing", tc)
        try:
            result = await tool.execute(tc.arguments, ctx)
        except ToolExecutionError as exc:
            self._in_flight_tools.pop(tc.tool_call_id, None)
            await self._fire_tool_failure_hook(tc, str(exc))
            yield await self._emit_tool_result(
                tc,
                str(exc),
                conversation,
                on_entry,
                is_error=True,
            )
            return
        except asyncio.CancelledError:
            # Leave in _in_flight_tools for finalize_cancelled_calls.
            raise
        except Exception as exc:
            self._in_flight_tools.pop(tc.tool_call_id, None)
            logger.exception("Unexpected error executing tool %s", tc.name)
            # Fire post_tool_failure hook.
            await self._fire_tool_failure_hook(tc, str(exc))
            yield await self._emit_tool_result(
                tc,
                f"Internal error: {exc}",
                conversation,
                on_entry,
                is_error=True,
            )
            return
        else:
            self._in_flight_tools.pop(tc.tool_call_id, None)

        # --- Result budget ---
        output = result.output
        if self._result_store and not result.is_error:
            max_chars = self._max_result_chars_override or tool.max_result_chars
            output = self._result_store.apply_budget(tc.name, output, max_chars)

        # --- post_tool_use hooks ---
        post_hooks = self._hook_registry.get_hooks(HookEvent.POST_TOOL_USE, tc.name, tc.arguments)
        if post_hooks:
            post_ctx = HookContext(
                tool_name=tc.name,
                tool_input=tc.arguments,
                tool_output=output,
            )
            await run_hooks(post_hooks, post_ctx)

        # Record + persist.
        event = await self._emit_tool_result(
            tc,
            output,
            conversation,
            on_entry,
            is_error=result.is_error,
            image_parts=result.image_parts,
            metadata=result.metadata,
        )
        yield event

        # Dispatch side-effect.
        if result.side_effect is not None and not result.is_error:
            async for evt in self.dispatch_side_effect(result.side_effect):
                yield evt

    # -- Side-effect dispatch ----------------------------------------------

    async def dispatch_side_effect(
        self,
        effect: Any,
        *,
        plan_mode_controller: Any = None,
        skill_setter: Callable[[str | None], None] | None = None,
        task_store: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        """Handle a tool's declared side-effect.

        Args:
            effect: The side-effect variant.
            plan_mode_controller: PlanModeController for plan effects.
                Falls back to the instance-level reference.
            skill_setter: Callback to set active skill prompt.
                Falls back to the instance-level reference.
            task_store: TaskStore for task persistence.
                Falls back to the instance-level reference.
        """
        # Fall back to stored references when not explicitly passed.
        if plan_mode_controller is None:
            plan_mode_controller = self._plan_mode_controller
        if skill_setter is None:
            skill_setter = self._skill_setter
        if task_store is None:
            task_store = self._task_store
        match effect:
            case EnterPlanMode() | ExitPlanMode():
                if plan_mode_controller is not None:
                    async for evt in plan_mode_controller.dispatch_side_effect(effect):
                        yield evt

            case SkillActivated(prompt=rendered):
                if skill_setter is not None:
                    skill_setter(rendered)
                logger.info("Skill prompt activated (%d chars)", len(rendered))
                return

            case TasksUpdated(tasks=items):
                if task_store is not None:
                    try:
                        task_store.save(list(items))
                    except Exception:
                        logger.exception("Failed to persist updated tasks")
                yield TaskUpdate(tasks=list(items))

            case FileChanged(file_path=path, change_type=ctype):
                await self._fire_file_changed_hook(path, ctype)

    # -- Hook helpers -------------------------------------------------------

    async def _fire_tool_failure_hook(self, tc: ToolUseContent, error_msg: str) -> None:
        """Fire ``post_tool_failure`` hook (fire-and-forget)."""
        try:
            hooks = self._hook_registry.get_hooks(
                HookEvent.POST_TOOL_FAILURE, tc.name, tc.arguments
            )
            if hooks:
                ctx = HookContext(
                    tool_name=tc.name,
                    tool_input=tc.arguments,
                    error_message=error_msg,
                )
                await run_hooks(hooks, ctx)
        except Exception:
            logger.exception("Error running post_tool_failure hook")

    async def _fire_permission_denied_hook(self, tc: ToolUseContent) -> None:
        """Fire ``permission_denied`` hook (fire-and-forget)."""
        try:
            hooks = self._hook_registry.get_hooks(HookEvent.PERMISSION_DENIED, tc.name, tc.arguments)
            if hooks:
                ctx = HookContext(tool_name=tc.name, tool_input=tc.arguments)
                await run_hooks(hooks, ctx)
        except Exception:
            logger.exception("Error running permission_denied hook")

    async def _fire_file_changed_hook(self, file_path: str, change_type: str) -> None:
        """Fire ``file_changed`` hook (fire-and-forget)."""
        try:
            hooks = self._hook_registry.get_hooks(HookEvent.FILE_CHANGED)
            if hooks:
                ctx = HookContext(file_path=file_path, change_type=change_type)
                await run_hooks(hooks, ctx)
        except Exception:
            logger.exception("Error running file_changed hook")

    # -- Parallel execution ------------------------------------------------

    def plan_groups(
        self,
        tool_calls: list[ToolUseContent],
    ) -> list[list[ExecutionSlot]]:
        """Classify tool calls and build execution groups."""
        slots: list[ExecutionSlot] = []
        for tc in tool_calls:
            tool = self._tool_registry.get(tc.name)
            if tool is None:
                slots.append(ExecutionSlot(tc=tc, hint=ConcurrencyHint.SERIAL, pre_approved=True))
                continue

            hint = tool.concurrency
            key = tool.concurrency_key(tc.arguments) if hint is ConcurrencyHint.KEYED else None
            decision = self._permission_engine.check(tool, tc.arguments)
            pre_approved = decision == PermissionDecision.ALLOW
            slots.append(ExecutionSlot(tc=tc, hint=hint, key=key, pre_approved=pre_approved))

        return plan_execution_groups(slots)

    async def execute_parallel_group(
        self,
        group: list[ExecutionSlot],
        ctx: ToolContext,
        conversation: Conversation,
        on_entry: Callable[[Any], None] | None,
        permission_callback: PermissionCallback | None,
        *,
        execute_agent_call: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        """Run a group of tool calls in parallel, yield events in order."""

        async def _collect(tc: ToolUseContent) -> list[StreamEvent]:
            events: list[StreamEvent] = []
            async for evt in self.execute(
                tc,
                ctx,
                conversation,
                on_entry,
                permission_callback,
                execute_agent_call=execute_agent_call,
            ):
                events.append(evt)
            return events

        results = await asyncio.gather(
            *[_collect(slot.tc) for slot in group],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            elif isinstance(result, BaseException):
                tc = group[i].tc
                yield await self._emit_tool_result(
                    tc,
                    f"Internal error during parallel execution: {result}",
                    conversation,
                    on_entry,
                    is_error=True,
                )
            else:
                for evt in result:
                    yield evt
