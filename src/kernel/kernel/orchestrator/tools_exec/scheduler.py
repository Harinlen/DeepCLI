"""Serial and concurrent scheduling for tool-call batches."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Literal

from kernel.llm.types import ToolUseContent
from kernel.orchestrator.permissions import PermissionCallback
from kernel.orchestrator.tools_exec.shared import SENTINEL, EventPair

if TYPE_CHECKING:
    from kernel.orchestrator.types import OrchestratorDeps
    from kernel.tool_authz import AuthorizeContext, PermissionMode, ToolAuthorizer
    from kernel.tools import Tool, ToolManager
    from kernel.tools.context import ToolContext

logger = logging.getLogger(__name__)


class ToolSchedulerMixin:
    """Execute ordered tool batches, with parallelism for safe tools."""

    _active_contexts: dict[str, asyncio.Event]
    _active_tasks: dict[str, asyncio.Task[None]]
    _deps: OrchestratorDeps
    _semaphore: asyncio.Semaphore

    if TYPE_CHECKING:

        def _build_tool_context(self, tool_source: ToolManager | None) -> ToolContext:
            """Build ToolContext for a scheduled call.

            Args:
                tool_source: ToolManager that owns shared tool state.

            Returns:
                ToolContext passed into ``tool.call``.
            """
            ...

        def _build_authorize_context(
            self,
            *,
            mode: PermissionMode,
        ) -> AuthorizeContext:
            """Build AuthorizeContext for a scheduled call.

            Args:
                mode: Permission mode used for authorization.

            Returns:
                AuthorizeContext passed into ToolAuthorizer.
            """
            ...

        def _run_one(
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
            """Run one resolved tool call.

            Args:
                tc: Original LLM tool-use block.
                tool: Resolved Tool implementation.
                tool_ctx: Tool execution context.
                auth_ctx: Tool authorization context.
                authorizer: Optional ToolAuthorizer subsystem.
                on_permission: Interactive permission callback.
                mode: Projected permission mode.

            Returns:
                Async generator for event/result pairs.
            """
            ...

        def _error_unknown_tool(
            self,
            tc: ToolUseContent,
        ) -> AsyncGenerator[EventPair, None]:
            """Emit error events for an unknown tool.

            Args:
                tc: Tool-use block whose name could not be resolved.

            Returns:
                Async generator for event/result pairs.
            """
            ...

    async def _execute_single(
        self,
        tc: ToolUseContent,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
    ) -> AsyncGenerator[EventPair, None]:
        """Execute a single tool call through the queue path.

        Args:
            tc: Tool-use block to execute.
            on_permission: Interactive permission callback.
            mode: Projected permission mode.

        Yields:
            Event/result pairs from the tool pipeline.
        """
        tool_source = self._deps.tool_source
        tool = tool_source.lookup(tc.name) if tool_source is not None else None
        if tool is None:
            async for item in self._error_unknown_tool(tc):
                yield item
            return

        tool_ctx = self._build_tool_context(tool_source)
        auth_ctx = self._build_authorize_context(mode=mode)
        self._active_contexts[tc.id] = tool_ctx.cancel_event

        queue: asyncio.Queue[EventPair | None] = asyncio.Queue()
        task = asyncio.create_task(
            self._run_one_to_queue(
                tc=tc,
                tool=tool,
                tool_ctx=tool_ctx,
                auth_ctx=auth_ctx,
                authorizer=self._deps.authorizer,
                on_permission=on_permission,
                mode=mode,
                queue=queue,
            ),
            name=f"tool-{tc.id}",
        )
        self._active_tasks[tc.id] = task

        try:
            while True:
                queued = await queue.get()
                if queued is SENTINEL:
                    break
                yield queued
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._active_contexts.pop(tc.id, None)
            self._active_tasks.pop(tc.id, None)

    async def _execute_batch_concurrent(
        self,
        batch: list[ToolUseContent],
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
    ) -> AsyncGenerator[EventPair, None]:
        """Execute a batch of tool calls concurrently.

        Args:
            batch: Adjacent tool-use blocks marked safe for concurrent execution.
            on_permission: Interactive permission callback.
            mode: Projected permission mode.

        Yields:
            Event/result pairs as individual tool queues produce them.
        """
        tool_source = self._deps.tool_source
        queues: list[asyncio.Queue[EventPair | None]] = []
        tasks: list[asyncio.Task[None]] = []

        for tc in batch:
            queue: asyncio.Queue[EventPair | None] = asyncio.Queue()
            queues.append(queue)

            tool = tool_source.lookup(tc.name) if tool_source is not None else None
            if tool is None:
                task = asyncio.create_task(
                    self._error_unknown_to_queue(tc, queue),
                    name=f"tool-{tc.id}-unknown",
                )
            else:
                tool_ctx = self._build_tool_context(tool_source)
                auth_ctx = self._build_authorize_context(mode=mode)
                self._active_contexts[tc.id] = tool_ctx.cancel_event
                task = asyncio.create_task(
                    self._run_one_to_queue(
                        tc=tc,
                        tool=tool,
                        tool_ctx=tool_ctx,
                        auth_ctx=auth_ctx,
                        authorizer=self._deps.authorizer,
                        on_permission=on_permission,
                        mode=mode,
                        queue=queue,
                    ),
                    name=f"tool-{tc.id}",
                )

            self._active_tasks[tc.id] = task
            tasks.append(task)

        try:
            async for pair in self._merge_queues(queues):
                yield pair
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for tc in batch:
                self._active_contexts.pop(tc.id, None)
                self._active_tasks.pop(tc.id, None)

    async def _run_one_to_queue(
        self,
        *,
        tc: ToolUseContent,
        tool: Tool,
        tool_ctx: ToolContext,
        auth_ctx: AuthorizeContext,
        authorizer: ToolAuthorizer | None,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
        queue: asyncio.Queue[EventPair | None],
    ) -> None:
        """Run one tool pipeline and write its output into a queue.

        Args:
            tc: Original LLM tool-use block.
            tool: Resolved Tool implementation.
            tool_ctx: Tool execution context.
            auth_ctx: Tool authorization context.
            authorizer: Optional ToolAuthorizer subsystem.
            on_permission: Interactive permission callback.
            mode: Projected permission mode.
            queue: Output queue receiving event/result pairs and sentinel.

        Returns:
            ``None``.
        """
        try:
            async with self._semaphore:
                async for pair in self._run_one(
                    tc=tc,
                    tool=tool,
                    tool_ctx=tool_ctx,
                    auth_ctx=auth_ctx,
                    authorizer=authorizer,
                    on_permission=on_permission,
                    mode=mode,
                ):
                    await queue.put(pair)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("tool %s failed in concurrent batch", tc.name)
        finally:
            await queue.put(SENTINEL)

    async def _error_unknown_to_queue(
        self,
        tc: ToolUseContent,
        queue: asyncio.Queue[EventPair | None],
    ) -> None:
        """Write unknown-tool error output into a queue.

        Args:
            tc: Tool-use block whose name was not registered.
            queue: Output queue receiving event/result pairs and sentinel.

        Returns:
            ``None``.
        """
        try:
            async for item in self._error_unknown_tool(tc):
                await queue.put(item)
        finally:
            await queue.put(SENTINEL)

    async def _merge_queues(
        self,
        queues: list[asyncio.Queue[EventPair | None]],
    ) -> AsyncGenerator[EventPair, None]:
        """Merge per-tool queues into one async event stream.

        Args:
            queues: Per-tool queues ending with ``SENTINEL``.

        Yields:
            Event/result pairs in completion order.
        """
        pending: dict[int, asyncio.Task[EventPair | None]] = {
            i: asyncio.create_task(queue.get(), name=f"merge-{i}") for i, queue in enumerate(queues)
        }
        active_indices = set(range(len(queues)))

        while active_indices:
            done, _ = await asyncio.wait(
                [pending[i] for i in active_indices],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                idx = next(i for i in active_indices if pending[i] is task)
                item = task.result()
                if item is SENTINEL:
                    active_indices.discard(idx)
                else:
                    yield item
                    pending[idx] = asyncio.create_task(
                        queues[idx].get(),
                        name=f"merge-{idx}",
                    )
