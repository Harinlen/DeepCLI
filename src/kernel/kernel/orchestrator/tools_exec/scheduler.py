"""Serial and concurrent scheduling for tool-call batches."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from kernel.llm.types import ToolUseContent
from kernel.orchestrator.permissions import PermissionCallback
from kernel.orchestrator.tools_exec.shared import SENTINEL, EventPair
from kernel.orchestrator.tools_exec.result_mapping import coerce_content
from kernel.tools.types import NestedToolResult

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
        tool_ctx.tool_use_id = tc.id
        auth_ctx = self._build_authorize_context(mode=mode)
        tool_ctx.run_nested_tool = self._make_nested_tool_runner(
            on_permission=on_permission,
            mode=mode,
        )
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
                tool_ctx.tool_use_id = tc.id
                auth_ctx = self._build_authorize_context(mode=mode)
                tool_ctx.run_nested_tool = self._make_nested_tool_runner(
                    on_permission=on_permission,
                    mode=mode,
                )
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

    def _make_nested_tool_runner(
        self,
        *,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
    ) -> Any:
        async def _run(tool_name: str, tool_input: dict[str, Any]) -> NestedToolResult:
            return await self._run_nested_tool(
                tool_name=tool_name,
                tool_input=tool_input,
                on_permission=on_permission,
                mode=mode,
            )

        return _run

    async def _run_nested_tool(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
    ) -> NestedToolResult:
        """Run one nested tool through the same pipeline as model tool calls."""
        from kernel.tools.repl.primitives import REPL_PRIMITIVE_TOOLS

        if tool_name == "REPL" or tool_name not in REPL_PRIMITIVE_TOOLS:
            return NestedToolResult(
                tool_name=tool_name,
                text=f"tool {tool_name!r} is not available inside REPL",
                is_error=True,
            )

        tool_source = self._deps.tool_source
        tool = tool_source.lookup(tool_name) if tool_source is not None else None
        if tool is None:
            return NestedToolResult(
                tool_name=tool_name,
                text=f"tool {tool_name!r} is not registered",
                is_error=True,
            )

        nested_input = dict(tool_input)
        repl_cwd = nested_input.pop("__repl_cwd", None)

        tc = ToolUseContent(
            id=f"repl-nested-{uuid.uuid4().hex}",
            name=tool_name,
            input=nested_input,
        )
        tool_ctx = self._build_tool_context(tool_source)
        if isinstance(repl_cwd, str) and repl_cwd.strip():
            tool_ctx.cwd = Path(repl_cwd).expanduser()
        tool_ctx.tool_use_id = tc.id
        tool_ctx.run_nested_tool = self._make_nested_tool_runner(
            on_permission=on_permission,
            mode=mode,
        )
        auth_ctx = self._build_authorize_context(mode=mode)

        texts: list[str] = []
        is_error = False
        async for _event, llm_result in self._run_one(
            tc=tc,
            tool=tool,
            tool_ctx=tool_ctx,
            auth_ctx=auth_ctx,
            authorizer=self._deps.authorizer,
            on_permission=on_permission,
            mode=mode,
        ):
            if llm_result is None:
                continue
            is_error = is_error or llm_result.is_error
            content = llm_result.content
            if isinstance(content, str):
                texts.append(content)
            else:
                coerced = coerce_content(list(content))
                texts.append(coerced if isinstance(coerced, str) else str(coerced))

        return NestedToolResult(
            tool_name=tool.name,
            text="\n".join(part for part in texts if part).strip() or "(no output)",
            is_error=is_error,
        )

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
