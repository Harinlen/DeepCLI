"""Public ToolExecutor facade."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kernel.llm.types import ToolUseContent
from kernel.orchestrator.permissions import PermissionCallback
from kernel.orchestrator.tools_exec.authorization import ToolAuthorizationMixin
from kernel.orchestrator.tools_exec.context import ToolContextMixin
from kernel.orchestrator.tools_exec.file_touch import FileTouchMixin
from kernel.orchestrator.tools_exec.hooks import ToolHookMixin
from kernel.orchestrator.tools_exec.partition import partition_tool_calls
from kernel.orchestrator.tools_exec.pipeline import ToolPipelineMixin
from kernel.orchestrator.tools_exec.result_mapping import ToolResultMappingMixin
from kernel.orchestrator.tools_exec.scheduler import ToolSchedulerMixin
from kernel.orchestrator.tools_exec.shared import DEFAULT_MAX_CONCURRENCY, EventPair
from kernel.orchestrator.types import OrchestratorDeps

if TYPE_CHECKING:
    from collections.abc import Callable

    from kernel.orchestrator.events import OrchestratorEvent
    from kernel.protocol.interfaces.contracts.content_block import ContentBlock
    from kernel.tools.context import ToolContext

    SpawnSubagent = Callable[
        [str, list[ContentBlock]],
        AsyncGenerator[OrchestratorEvent, None],
    ]
    ContextChanged = Callable[[ToolContext], None]


class ToolExecutor(
    ToolSchedulerMixin,
    ToolPipelineMixin,
    ToolAuthorizationMixin,
    ToolContextMixin,
    ToolHookMixin,
    FileTouchMixin,
    ToolResultMappingMixin,
):
    """Streaming-shaped executor with parallel batch support.

    The executor accepts tool calls incrementally while the provider stream is
    still open, then runs them in serial/concurrent batches once finalized.
    """

    def __init__(
        self,
        deps: OrchestratorDeps,
        *,
        session_id: str,
        cwd: Path,
        agent_depth: int = 0,
        agent_id: str | None = None,
        spawn_subagent: SpawnSubagent | None = None,
        set_plan_mode: Callable[[bool], None] | None = None,
        set_mode: Callable[[str], None] | None = None,
        on_context_changed: ContextChanged | None = None,
        streaming: bool = False,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        """Create a per-turn tool executor.

        Args:
            deps: Orchestrator dependency bundle.
            session_id: Session that owns the tool calls.
            cwd: Current working directory.
            agent_depth: Root/sub-agent depth.
            agent_id: Optional child-agent id.
            spawn_subagent: Optional child-agent spawn closure.
            set_plan_mode: Optional legacy plan-mode setter.
            set_mode: Optional permission-mode setter.
            on_context_changed: Callback for tool context modifiers.
            streaming: Whether calls may arrive before provider stream ends.
            max_concurrency: Maximum number of concurrent safe tool calls.
        """
        self._deps = deps
        self._session_id = session_id
        self._cwd = cwd
        self._agent_depth = agent_depth
        self._agent_id = agent_id
        self._spawn_subagent = spawn_subagent
        self._set_plan_mode = set_plan_mode
        self._set_mode = set_mode
        self._on_context_changed = on_context_changed
        self._streaming = streaming
        self._max_concurrency = max_concurrency
        self._allow_all_warned = False
        self._queue: list[ToolUseContent] = []
        self._finalized = False
        self._discarded = False
        self._permission_lock = asyncio.Lock()
        self._active_contexts: dict[str, asyncio.Event] = {}
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def add_tool(self, tool_use: ToolUseContent) -> None:
        """Accept a tool_use block for later execution.

        Args:
            tool_use: LLM-emitted tool-use block.

        Returns:
            ``None``.
        """
        if not self._discarded:
            self._queue.append(tool_use)

    def finalize_stream(self) -> None:
        """Signal that no more tool_use blocks will arrive.

        Returns:
            ``None``.
        """
        self._finalized = True

    def discard(self) -> None:
        """Cancel all in-flight tools and clear the queue.

        Returns:
            ``None``.
        """
        self._discarded = True
        self._queue.clear()
        for cancel_event in self._active_contexts.values():
            cancel_event.set()
        for task in self._active_tasks.values():
            task.cancel()
        self._active_contexts.clear()
        self._active_tasks.clear()

    async def results(
        self,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"] = "default",
    ) -> AsyncGenerator[EventPair, None]:
        """Yield event/result pairs for all queued tools.

        Args:
            on_permission: Interactive permission callback.
            mode: Permission mode projected for authorization.

        Yields:
            Tool lifecycle event plus optional LLM-facing tool result.

        Raises:
            RuntimeError: If called before ``finalize_stream()``.
        """
        if not self._finalized:
            raise RuntimeError("results() called before finalize_stream()")

        tool_source = self._deps.tool_source
        lookup = tool_source.lookup if tool_source is not None else None
        batches = partition_tool_calls(self._queue, lookup)

        for batch in batches:
            if self._discarded:
                return
            if len(batch) == 1:
                async for pair in self._execute_single(batch[0], on_permission, mode):
                    yield pair
            else:
                async for pair in self._execute_batch_concurrent(batch, on_permission, mode):
                    yield pair

    async def run(
        self,
        tool_calls: list[ToolUseContent],
        on_permission: PermissionCallback,
        plan_mode: bool = False,
        mode: Literal["default", "plan", "bypass"] = "default",
    ) -> AsyncGenerator[EventPair, None]:
        """Legacy interface wrapping add_tool/finalize/results.

        Args:
            tool_calls: Complete list of tool calls to execute.
            on_permission: Interactive permission callback.
            plan_mode: Legacy boolean plan-mode override.
            mode: Permission mode projected for authorization.

        Returns:
            Async generator yielding tool event/result pairs.
        """
        for tc in tool_calls:
            self.add_tool(tc)
        self.finalize_stream()
        effective_mode: Literal["default", "plan", "bypass"] = "plan" if plan_mode else mode
        return self.results(on_permission, effective_mode)
