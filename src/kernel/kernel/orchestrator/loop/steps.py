"""Helper steps used by the Orchestrator query loop."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from kernel.hooks.types import HookEvent
from kernel.llm.types import PromptSection, ToolSchema
from kernel.orchestrator.constants import MAX_OUTPUT_TOKEN_RETRIES
from kernel.orchestrator.events import CompactionEvent, HistorySnapshot, OrchestratorEvent
from kernel.orchestrator.notifications import format_monitor_notification, format_task_notification
from kernel.orchestrator.prompt_runtime import (
    dump_system_prompt,
    inject_plan_mode_prompts,
    inject_session_guidance,
)
from kernel.orchestrator.reminders import drain_pending_reminders, extract_text, format_reminders
from kernel.orchestrator.runtime import QueryRuntime, system_reminder_section
from kernel.orchestrator.tool_executor import ToolExecutor
from kernel.orchestrator.types import StopReason

logger = logging.getLogger(__name__)


async def setup_turn(
    orchestrator: QueryRuntime,
    prompt: list[Any],
) -> tuple[str, list[str], bool]:
    """Drain reminders and fire the user_prompt_submit hook.

    Args:
        orchestrator: Runtime holding deps and hook state.
        prompt: Raw user content blocks for this turn.

    Returns:
        ``(prompt_text, reminders, blocked)`` after hook mutation.
    """
    reminders = drain_pending_reminders(orchestrator._deps)
    if orchestrator._agent_id and orchestrator._deps.task_registry:
        reminders.extend(
            f"Message from parent agent:\n{msg}"
            for msg in orchestrator._deps.task_registry.drain_messages(orchestrator._agent_id)
        )
    prompt_text = extract_text(prompt)
    if reminders:
        prompt_text = format_reminders(reminders, prompts=orchestrator._deps.prompts) + prompt_text
    blocked, hook_ctx = await orchestrator._fire_hook(
        event=HookEvent.USER_PROMPT_SUBMIT,
        user_text=prompt_text,
    )
    if blocked:
        orchestrator._stop_reason = StopReason.hook_blocked
        return prompt_text, reminders, True
    if hook_ctx.user_text is not None and hook_ctx.user_text != prompt_text:
        prompt_text = hook_ctx.user_text
    return prompt_text, reminders, False


async def prepare_history(
    orchestrator: QueryRuntime,
) -> AsyncGenerator[OrchestratorEvent, None]:
    """Run cheap-to-expensive compaction passes.

    Args:
        orchestrator: Runtime whose history may need compaction.

    Yields:
        Compaction and history-snapshot events when history is rewritten.
    """
    orchestrator._compactor.snip(orchestrator._history)
    threshold = orchestrator._compaction_threshold()
    if orchestrator._history.token_count > threshold:
        orchestrator._compactor.microcompact(orchestrator._history)
    if orchestrator._history.token_count > threshold:
        before = orchestrator._history.token_count
        await orchestrator._compactor.compact(orchestrator._history)
        yield CompactionEvent(tokens_before=before, tokens_after=orchestrator._history.token_count)
        yield HistorySnapshot(messages=list(orchestrator._history.messages))


async def build_prompt(
    orchestrator: QueryRuntime,
    prompt_text: str,
    turn_index: int,
) -> tuple[list[PromptSection], list[ToolSchema]]:
    """Build the system prompt and snapshot visible tools.

    Args:
        orchestrator: Runtime that owns prompt builder and tool source.
        prompt_text: Hook-mutated prompt text for prompt-context decisions.
        turn_index: One-based loop iteration index for dump/debug behavior.

    Returns:
        Rendered system prompt sections and visible tool schemas.
    """
    system_prompt = await orchestrator._prompt_builder.build(
        prompt_text,
        cwd=orchestrator._cwd,
        model=orchestrator._config.model,
        language=orchestrator._config.language,
    )
    if turn_index == 1:
        dump_system_prompt(system_prompt, orchestrator._session_id, orchestrator._config.model)
    tool_schemas: list[ToolSchema] = []
    snapshot = None
    if orchestrator._deps.tool_source is not None:
        try:
            snapshot = orchestrator._deps.tool_source.snapshot_for_session(
                session_id=orchestrator._session_id,
                plan_mode=orchestrator.plan_mode,
            )
            tool_schemas = list(snapshot.schemas)
            if snapshot.deferred_listing:
                system_prompt.append(system_reminder_section(snapshot.deferred_listing))
        except Exception:
            logger.exception(
                "Orchestrator[%s]: tool_source.snapshot failed", orchestrator._session_id
            )
    names = (
        ({schema.name for schema in snapshot.schemas} | snapshot.deferred_names)
        if snapshot
        else set()
    )
    inject_session_guidance(orchestrator, system_prompt, names)
    inject_plan_mode_prompts(orchestrator, system_prompt)
    return system_prompt, tool_schemas


def make_executor(orchestrator: QueryRuntime) -> ToolExecutor:
    """Create the per-turn ToolExecutor.

    Args:
        orchestrator: Runtime whose deps and cwd seed tool execution.

    Returns:
        Configured ``ToolExecutor`` for the current model response.
    """
    set_mode = orchestrator._deps.set_mode or orchestrator.set_mode

    def handle_context_change(new_ctx: Any) -> None:
        """Apply ToolContext cwd mutations back to the runtime.

        Args:
            new_ctx: ToolContext returned by a tool ``context_modifier``.

        Returns:
            ``None``.
        """
        orchestrator._cwd = new_ctx.cwd
        executor._cwd = new_ctx.cwd
        if (git := getattr(orchestrator._deps, "git", None)) is not None:
            git.invalidate_context(orchestrator._session_id)

    executor = ToolExecutor(
        deps=orchestrator._deps,
        session_id=orchestrator._session_id,
        cwd=orchestrator._cwd,
        agent_depth=orchestrator._depth,
        agent_id=orchestrator._agent_id,
        spawn_subagent=orchestrator._make_spawn_subagent(),
        set_plan_mode=orchestrator.set_plan_mode,
        set_mode=set_mode,
        on_context_changed=handle_context_change,
        streaming=orchestrator._config.streaming_tools,
    )
    return executor


async def handle_stop(
    orchestrator: QueryRuntime,
    last_stop_reason: str | None,
    max_tokens_retries: int,
    token_budget: int | None,
) -> str:
    """Handle no-tool stop branch.

    Args:
        orchestrator: Runtime whose stop hooks and token usage are inspected.
        last_stop_reason: Provider stop reason from the latest stream.
        max_tokens_retries: Number of output-limit retries already attempted.
        token_budget: Optional input+output budget for the query.

    Returns:
        One of ``"retry"``, ``"budget"``, or ``"stop"``.
    """
    if last_stop_reason == "max_tokens" and max_tokens_retries < MAX_OUTPUT_TOKEN_RETRIES:
        orchestrator._history.pop_last_assistant()
        return "retry"
    await orchestrator._fire_hook(
        event=HookEvent.STOP,
        stop_reason=last_stop_reason or "end_turn",
        message_count=len(orchestrator._history.messages),
        token_estimate=orchestrator._history.token_count,
    )
    if (
        token_budget is not None
        and orchestrator._turn_input_tokens + orchestrator._turn_output_tokens >= token_budget
    ):
        return "budget"
    return "stop"


def budget_message(orchestrator: QueryRuntime, token_budget: int) -> str:
    """Format token-budget exceeded text.

    Args:
        orchestrator: Runtime containing last-turn token usage.
        token_budget: Budget that was exceeded.

    Returns:
        User-visible budget error message.
    """
    used = orchestrator._turn_input_tokens + orchestrator._turn_output_tokens
    return f"Token budget exceeded: {used} tokens used, budget was {token_budget}"


def drain_task_notifications(orchestrator: QueryRuntime) -> None:
    """Queue task and monitor notifications as reminders.

    Args:
        orchestrator: Runtime whose task registry may contain notifications.

    Returns:
        ``None``.
    """
    registry = orchestrator._deps.task_registry
    if registry is None:
        return
    for task_id in registry.drain_notifications(agent_id=orchestrator._agent_id):
        task = registry.get(task_id)
        if task is not None and orchestrator._deps.queue_reminders is not None:
            orchestrator._deps.queue_reminders([format_task_notification(task)])
    registry.evict_terminal()
    for task_id, lines in registry.drain_monitor_lines(agent_id=orchestrator._agent_id).items():
        if lines and orchestrator._deps.queue_reminders is not None:
            orchestrator._deps.queue_reminders([format_monitor_notification(task_id, lines)])
