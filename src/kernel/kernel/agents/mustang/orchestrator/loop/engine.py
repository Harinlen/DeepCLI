"""Main LLM/tool query loop for StandardOrchestrator."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal

from kernel.agents.mustang.hooks.types import HookEvent
from kernel.agents.mustang.llm.types import (
    StreamError,
    TextChunk,
    ThoughtChunk,
    ToolResultContent,
    ToolUseChunk,
    ToolUseContent,
    UsageChunk,
)
from kernel.agents.mustang.llm_provider.errors import MediaSizeError, PromptTooLongError, ProviderError
from kernel.agents.mustang.orchestrator.constants import MAX_REACTIVE_RETRIES, MAX_TOKENS_ESCALATED
from kernel.agents.mustang.orchestrator.events import (
    CancelledEvent,
    CompactionEvent,
    HistoryAppend,
    HistorySnapshot,
    OrchestratorEvent,
    QueryError,
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    UserPromptBlocked,
)
from kernel.agents.mustang.orchestrator.loop.steps import (
    budget_message,
    build_prompt,
    drain_task_notifications,
    handle_stop,
    make_executor,
    prepare_history,
    setup_turn,
)
from kernel.agents.mustang.orchestrator.reminders import to_text_content
from kernel.agents.mustang.orchestrator.runtime import QueryRuntime
from kernel.agents.mustang.orchestrator.types import PermissionCallback, StopReason

logger = logging.getLogger(__name__)

MAX_TRANSIENT_STREAM_RETRIES = 2
TRANSIENT_STREAM_ERROR_CODES = frozenset({"transient_transport"})
INTERRUPTED_TOOL_RESULT = "Interrupted before tool result was recorded"


@dataclass
class TurnState:
    """Mutable retry/counter state for one user prompt."""

    index: int = 0
    reactive_retries: int = 0
    max_tokens_override: int | None = None
    max_tokens_retries: int = 0
    transient_stream_retries: int = 0
    protocol_repair_retries: int = 0

    def start_next_iteration(self) -> None:
        """Advance to the next LLM/tool loop iteration.

        Returns:
            ``None``.
        """
        self.index += 1

    def rewind_retry_iteration(self) -> None:
        """Retry the same logical turn after provider recovery.

        Returns:
            ``None``.
        """
        self.index -= 1


@dataclass
class StreamAccumulator:
    """Collected chunks from one provider stream."""

    text_parts: list[str] = field(default_factory=list)
    thought_chunks: list[ThoughtChunk] = field(default_factory=list)
    tool_calls: list[ToolUseContent] = field(default_factory=list)
    last_stop_reason: str | None = None

    @property
    def assistant_text(self) -> str:
        """Assistant text assembled from streamed deltas.

        Returns:
            Concatenated visible assistant text.
        """
        return "".join(self.text_parts)

    @property
    def has_sampled_output(self) -> bool:
        """Whether POST_SAMPLING should fire for this stream.

        Returns:
            ``True`` when the stream produced text or tool calls.
        """
        return bool(self.text_parts or self.tool_calls)


async def run_query(
    orchestrator: QueryRuntime,
    prompt: list[Any],
    *,
    on_permission: PermissionCallback,
    token_budget: int | None = None,
    max_turns: int = 0,
) -> AsyncGenerator[OrchestratorEvent, None]:
    """Run one prompt turn through the LLM/tool loop.

    Args:
        orchestrator: Runtime facade holding history, deps, config, and state.
        prompt: User content blocks for the turn.
        on_permission: Callback for interactive tool authorization.
        token_budget: Optional input+output budget for this query.
        max_turns: Maximum model/tool loop iterations; ``0`` means unlimited.

    Yields:
        Streaming, tool, history, compaction, cancellation, and error events.
    """
    logger.info("Orchestrator[%s]: _run_query START", orchestrator._session_id)
    orchestrator._turn_input_tokens = 0
    orchestrator._turn_output_tokens = 0
    try:
        prompt_text, reminders, blocked = await setup_turn(orchestrator, prompt)
        if blocked:
            yield UserPromptBlocked(reason="user_prompt_submit hook blocked")
            return
        repaired_ids = orchestrator._history.repair_orphan_tool_results(INTERRUPTED_TOOL_RESULT)
        if repaired_ids:
            logger.info(
                "Orchestrator[%s]: repaired %d pending tool result(s) before new turn",
                orchestrator._session_id,
                len(repaired_ids),
            )
            yield HistorySnapshot(messages=list(orchestrator._history.messages))
        orchestrator._history.append_user(
            to_text_content(prompt, reminders=reminders, prompts=orchestrator._deps.prompts)
        )
        yield HistoryAppend(message=orchestrator._history.messages[-1])

        turn = TurnState()

        while True:
            turn.start_next_iteration()
            if max_turns > 0 and turn.index > max_turns:
                orchestrator._stop_reason = StopReason.max_turns
                return

            async for event in prepare_history(orchestrator):
                yield event
            system_prompt, tool_schemas = await build_prompt(
                orchestrator,
                prompt_text,
                turn.index,
            )
            executor = make_executor(orchestrator)
            streaming_tools = orchestrator._config.streaming_tools
            stream_result = StreamAccumulator()
            retry_transient_stream = False

            try:
                stream = await orchestrator._deps.provider.stream(
                    system=system_prompt,
                    messages=orchestrator._history.messages,
                    tool_schemas=tool_schemas,
                    model=orchestrator._config.model,
                    temperature=orchestrator._config.temperature,
                    thinking=orchestrator._config.thinking,
                    max_tokens=turn.max_tokens_override,
                )
                async for chunk in stream:
                    if isinstance(chunk, TextChunk):
                        stream_result.text_parts.append(chunk.content)
                        yield TextDelta(content=chunk.content)
                    elif isinstance(chunk, ThoughtChunk):
                        stream_result.thought_chunks.append(chunk)
                        if chunk.content:
                            yield ThoughtDelta(content=chunk.content)
                    elif isinstance(chunk, ToolUseChunk):
                        tool_use = ToolUseContent(id=chunk.id, name=chunk.name, input=chunk.input)
                        stream_result.tool_calls.append(tool_use)
                        if streaming_tools:
                            executor.add_tool(tool_use)
                    elif isinstance(chunk, UsageChunk):
                        orchestrator._history.update_token_count(
                            chunk.input_tokens,
                            chunk.output_tokens,
                        )
                        orchestrator._turn_input_tokens += chunk.input_tokens
                        orchestrator._turn_output_tokens += chunk.output_tokens
                        stream_result.last_stop_reason = chunk.stop_reason
                    elif isinstance(chunk, StreamError):
                        executor.discard()
                        if (
                            chunk.code in TRANSIENT_STREAM_ERROR_CODES
                            and turn.transient_stream_retries < MAX_TRANSIENT_STREAM_RETRIES
                            and not stream_result.has_sampled_output
                        ):
                            turn.transient_stream_retries += 1
                            await asyncio.sleep(0.5 * turn.transient_stream_retries)
                            turn.rewind_retry_iteration()
                            logger.info(
                                "Orchestrator[%s]: retrying transient stream error "
                                "attempt=%d/%d message=%s",
                                orchestrator._session_id,
                                turn.transient_stream_retries,
                                MAX_TRANSIENT_STREAM_RETRIES,
                                chunk.message,
                            )
                            retry_transient_stream = True
                            break
                        yield QueryError(message=chunk.message, code=chunk.code)
                        orchestrator._stop_reason = StopReason.error
                        return
            except PromptTooLongError as exc:
                if turn.reactive_retries >= MAX_REACTIVE_RETRIES:
                    yield QueryError(message=str(exc), code="prompt_too_long")
                    orchestrator._stop_reason = StopReason.error
                    return
                turn.reactive_retries += 1
                before = orchestrator._history.token_count
                await orchestrator._compactor.compact(orchestrator._history)
                yield CompactionEvent(
                    tokens_before=before,
                    tokens_after=orchestrator._history.token_count,
                )
                turn.rewind_retry_iteration()
                continue
            except MediaSizeError as exc:
                if turn.reactive_retries >= MAX_REACTIVE_RETRIES:
                    yield QueryError(message=str(exc), code="media_size")
                    orchestrator._stop_reason = StopReason.error
                    return
                turn.reactive_retries += 1
                stripped = orchestrator._compactor.strip_media(orchestrator._history)
                if stripped > 0:
                    before = orchestrator._history.token_count
                    await orchestrator._compactor.compact(orchestrator._history)
                    yield CompactionEvent(
                        tokens_before=before, tokens_after=orchestrator._history.token_count
                    )
                turn.rewind_retry_iteration()
                continue
            except ProviderError as exc:
                if (
                    _is_tool_protocol_error(exc)
                    and turn.protocol_repair_retries < 1
                    and orchestrator._history.repair_orphan_tool_results(INTERRUPTED_TOOL_RESULT)
                ):
                    turn.protocol_repair_retries += 1
                    turn.rewind_retry_iteration()
                    logger.info(
                        "Orchestrator[%s]: repaired orphan tool result(s) after provider "
                        "protocol rejection; retrying turn",
                        orchestrator._session_id,
                    )
                    yield HistorySnapshot(messages=list(orchestrator._history.messages))
                    continue
                yield QueryError(message=str(exc))
                orchestrator._stop_reason = StopReason.error
                return

            if retry_transient_stream:
                continue

            if stream_result.has_sampled_output:
                await orchestrator._fire_hook(event=HookEvent.POST_SAMPLING)
            await asyncio.sleep(0)

            before_append_count = len(orchestrator._history.messages)
            orchestrator._history.append_assistant(
                text=stream_result.assistant_text,
                thoughts=list(stream_result.thought_chunks),
                tool_calls=stream_result.tool_calls,
            )
            if len(orchestrator._history.messages) > before_append_count:
                yield HistoryAppend(message=orchestrator._history.messages[-1])

            if not stream_result.tool_calls:
                recovered = await handle_stop(
                    orchestrator,
                    stream_result.last_stop_reason,
                    turn.max_tokens_retries,
                    token_budget,
                )
                if recovered == "retry":
                    turn.max_tokens_retries += 1
                    turn.max_tokens_override = MAX_TOKENS_ESCALATED
                    continue
                if recovered == "budget":
                    yield QueryError(
                        message=budget_message(orchestrator, token_budget or 0),
                        code="token_budget_exceeded",
                    )
                    orchestrator._stop_reason = StopReason.budget_exceeded
                    return
                orchestrator._stop_reason = StopReason.end_turn
                return

            if not streaming_tools:
                for tool_use in stream_result.tool_calls:
                    executor.add_tool(tool_use)
            executor.finalize_stream()
            results: list[ToolResultContent] = []
            mode = _tool_permission_mode(orchestrator)
            async for event, result in executor.results(on_permission=on_permission, mode=mode):
                if isinstance(event, ToolCallStart):
                    orchestrator._history.record_tool_kind(event.id, event.kind)
                yield event
                if result is not None:
                    results.append(result)
            await asyncio.sleep(0)
            drain_task_notifications(orchestrator)
            before_append_count = len(orchestrator._history.messages)
            orchestrator._history.append_tool_results(results)
            if len(orchestrator._history.messages) > before_append_count:
                yield HistoryAppend(message=orchestrator._history.messages[-1])
    except asyncio.CancelledError:
        orchestrator._stop_reason = StopReason.cancelled
        orphan_ids = orchestrator._history.append_interrupted_tool_results("Interrupted by user")
        if orphan_ids:
            yield HistoryAppend(message=orchestrator._history.messages[-1])
        yield CancelledEvent()


def _tool_permission_mode(
    orchestrator: QueryRuntime,
) -> Literal["default", "plan", "bypass"]:
    """Project Orchestrator modes onto the ToolAuthorizer's narrower modes.

    Args:
        orchestrator: Runtime whose current mode should be projected.

    Returns:
        Permission mode accepted by ``ToolExecutor.results``.
    """
    if orchestrator._mode == "plan":
        return "plan"
    if orchestrator._mode == "bypass":
        return "bypass"
    return "default"


def _is_tool_protocol_error(exc: ProviderError) -> bool:
    message = str(exc).lower()
    missing_result = (
        "tool_calls" in message
        and "tool messages" in message
        and ("must be followed" in message or "insufficient tool messages" in message)
    )
    orphan_tool = (
        "role 'tool'" in message and "preceding message" in message and "tool_calls" in message
    )
    return missing_result or orphan_tool
