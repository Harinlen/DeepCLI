"""ToolExecutor — parallel batch execution, partitioning, and streaming interface."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kernel.llm.types import ToolUseContent
from kernel.orchestrator.events import ToolCallResult as ToolCallResultEvent
from kernel.orchestrator.events import ToolCallStart
from kernel.orchestrator.tool_executor import ToolExecutor, partition_tool_calls
from kernel.orchestrator.types import OrchestratorDeps, ToolKind
from kernel.tool_authz.types import (
    AuthorizeContext,
    PermissionAllow,
    PermissionAsk,
    PermissionSuggestionBtn,
    ReasonDefaultRisk,
)
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _SafeTool(Tool[dict[str, Any], str]):
    """Concurrency-safe read tool that sleeps for a configurable duration."""

    name = "SafeRead"
    description = "safe read"
    kind = ToolKind.read  # is_read_only → is_concurrency_safe = True

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="low", default_decision="allow", reason="")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        pass

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        from kernel.protocol.interfaces.contracts.text_block import TextBlock
        from kernel.tools.types import TextDisplay

        delay = input.get("delay", 0)
        if delay:
            await asyncio.sleep(delay)

        text = f"safe-result:{input.get('id', '')}"
        yield ToolCallResult(
            data=text,
            llm_content=[TextBlock(type="text", text=text)],
            display=TextDisplay(text=text),
        )


class _UnsafeTool(Tool[dict[str, Any], str]):
    """Non-concurrency-safe tool."""

    name = "UnsafeExec"
    description = "unsafe exec"
    kind = ToolKind.execute  # is_concurrency_safe = False

    def default_risk(self, input: dict[str, Any], ctx: Any) -> PermissionSuggestion:
        return PermissionSuggestion(risk="low", default_decision="allow", reason="")

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        pass

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        from kernel.protocol.interfaces.contracts.text_block import TextBlock
        from kernel.tools.types import TextDisplay

        delay = input.get("delay", 0)
        if delay:
            await asyncio.sleep(delay)

        text = f"unsafe-result:{input.get('id', '')}"
        yield ToolCallResult(
            data=text,
            llm_content=[TextBlock(type="text", text=text)],
            display=TextDisplay(text=text),
        )


class _StubAuthorizer:
    """Always-allow authorizer."""

    async def authorize(
        self, *, tool: Tool, tool_input: dict[str, Any], ctx: AuthorizeContext
    ) -> PermissionAllow:
        return PermissionAllow(
            decision_reason=ReasonDefaultRisk(risk="low", reason="test", tool_name=tool.name),
        )

    def grant(self, *, tool: Tool, tool_input: dict[str, Any], ctx: AuthorizeContext) -> None:
        pass


class _AskAuthorizer:
    """Authorizer that always returns PermissionAsk (requires on_permission round-trip)."""

    async def authorize(
        self, *, tool: Tool, tool_input: dict[str, Any], ctx: AuthorizeContext
    ) -> PermissionAsk:
        return PermissionAsk(
            message=f"approve {tool.name}?",
            decision_reason=ReasonDefaultRisk(risk="medium", reason="test", tool_name=tool.name),
        )

    def grant(self, *, tool: Tool, tool_input: dict[str, Any], ctx: AuthorizeContext) -> None:
        pass


def _tool_source(*tools: Tool) -> MagicMock:
    """Build a mock ToolManager that resolves the given tools by name."""
    tool_map = {t.name: t for t in tools}
    src = MagicMock()
    src.lookup.side_effect = lambda name: tool_map.get(name)
    src.file_state.return_value = MagicMock()
    return src


def _deps(
    *tools: Tool,
    authorizer: Any = None,
) -> OrchestratorDeps:
    return OrchestratorDeps(
        provider=MagicMock(),
        tool_source=_tool_source(*tools),
        authorizer=authorizer or _StubAuthorizer(),
    )


def _tc(id_: str, name: str, **kw: Any) -> ToolUseContent:
    return ToolUseContent(id=id_, name=name, input=kw)


async def _collect_results(
    executor: ToolExecutor,
    on_permission: Any = None,
) -> list[Any]:
    """Finalize and drain all events from executor.results()."""

    async def _no_perm(req: Any) -> Any:
        raise AssertionError("unexpected on_permission call")

    events = []
    async for event, _result in executor.results(
        on_permission=on_permission or _no_perm,
        mode="default",
    ):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# partition_tool_calls
# ---------------------------------------------------------------------------


class TestPartitionToolCalls:
    def test_empty(self) -> None:
        assert partition_tool_calls([], None) == []

    def test_all_safe(self) -> None:
        safe = _SafeTool()
        calls = [_tc("1", "SafeRead"), _tc("2", "SafeRead"), _tc("3", "SafeRead")]
        batches = partition_tool_calls(calls, lambda name: safe if name == "SafeRead" else None)
        # All safe → one batch.
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_all_unsafe(self) -> None:
        unsafe = _UnsafeTool()
        calls = [_tc("1", "UnsafeExec"), _tc("2", "UnsafeExec")]
        batches = partition_tool_calls(
            calls, lambda name: unsafe if name == "UnsafeExec" else None
        )
        # Each unsafe is a singleton batch.
        assert len(batches) == 2
        assert all(len(b) == 1 for b in batches)

    def test_mixed(self) -> None:
        safe = _SafeTool()
        unsafe = _UnsafeTool()
        tool_map = {"SafeRead": safe, "UnsafeExec": unsafe}
        calls = [
            _tc("1", "SafeRead"),
            _tc("2", "SafeRead"),
            _tc("3", "UnsafeExec"),
            _tc("4", "SafeRead"),
            _tc("5", "UnsafeExec"),
        ]
        batches = partition_tool_calls(calls, lambda name: tool_map.get(name))
        assert len(batches) == 4
        assert [len(b) for b in batches] == [2, 1, 1, 1]

    def test_unknown_tool_treated_as_unsafe(self) -> None:
        calls = [_tc("1", "Unknown"), _tc("2", "Unknown")]
        batches = partition_tool_calls(calls, lambda name: None)
        assert len(batches) == 2
        assert all(len(b) == 1 for b in batches)


# ---------------------------------------------------------------------------
# Concurrent execution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_safe_tools_run_concurrently() -> None:
    """Three safe tools with 100ms delay each should complete in ~100ms, not ~300ms."""
    safe = _SafeTool()
    executor = ToolExecutor(
        _deps(safe), session_id="s", cwd=Path.cwd()
    )
    for i in range(3):
        executor.add_tool(_tc(str(i), "SafeRead", id=str(i), delay=0.1))
    executor.finalize_stream()

    t0 = time.monotonic()
    events = await _collect_results(executor)
    elapsed = time.monotonic() - t0

    # Should have 3 ToolCallStart + 3 ToolCallResult events.
    starts = [e for e in events if isinstance(e, ToolCallStart)]
    results = [e for e in events if isinstance(e, ToolCallResultEvent)]
    assert len(starts) == 3
    assert len(results) == 3

    # Concurrent: total time should be well under 3×100ms.
    assert elapsed < 0.25, f"expected concurrent execution, took {elapsed:.3f}s"


@pytest.mark.anyio
async def test_unsafe_tool_runs_alone() -> None:
    """Unsafe tools execute in isolation — no overlap with other tools."""
    safe = _SafeTool()
    unsafe = _UnsafeTool()
    executor = ToolExecutor(
        _deps(safe, unsafe), session_id="s", cwd=Path.cwd()
    )

    # [safe, safe, unsafe, safe]
    executor.add_tool(_tc("1", "SafeRead", id="1", delay=0.05))
    executor.add_tool(_tc("2", "SafeRead", id="2", delay=0.05))
    executor.add_tool(_tc("3", "UnsafeExec", id="3", delay=0.05))
    executor.add_tool(_tc("4", "SafeRead", id="4", delay=0.05))
    executor.finalize_stream()

    events = await _collect_results(executor)

    # All 4 tools should produce Start + Result events.
    starts = [e for e in events if isinstance(e, ToolCallStart)]
    results = [e for e in events if isinstance(e, ToolCallResultEvent)]
    assert len(starts) == 4
    assert len(results) == 4

    # Verify ordering: the unsafe tool's Start must come after the first
    # safe batch completes (i.e. after both safe Results).
    # IDs "1" and "2" are batch 1 (safe), "3" is batch 2 (unsafe), "4" is batch 3 (safe).
    # The unsafe Start ("3") must appear after Results for "1" and "2".
    idx_unsafe_start = next(i for i, e in enumerate(events) if isinstance(e, ToolCallStart) and e.id == "3")
    safe_batch1_results = [
        i for i, e in enumerate(events)
        if isinstance(e, ToolCallResultEvent) and e.id in ("1", "2")
    ]
    assert all(r < idx_unsafe_start for r in safe_batch1_results)


@pytest.mark.anyio
async def test_per_tool_event_ordering() -> None:
    """ToolCallStart always precedes ToolCallResult for the same tool_use_id."""
    safe = _SafeTool()
    executor = ToolExecutor(
        _deps(safe), session_id="s", cwd=Path.cwd()
    )
    for i in range(4):
        executor.add_tool(_tc(str(i), "SafeRead", id=str(i), delay=0.02))
    executor.finalize_stream()

    events = await _collect_results(executor)

    for tool_id in ("0", "1", "2", "3"):
        indices = [
            i for i, e in enumerate(events)
            if (isinstance(e, ToolCallStart) and e.id == tool_id)
            or (isinstance(e, ToolCallResultEvent) and e.id == tool_id)
        ]
        assert len(indices) == 2
        start_idx, result_idx = indices
        assert isinstance(events[start_idx], ToolCallStart)
        assert isinstance(events[result_idx], ToolCallResultEvent)
        assert start_idx < result_idx


@pytest.mark.anyio
async def test_permission_serialization() -> None:
    """When concurrent safe tools both need ask, on_permission is never called concurrently."""
    from kernel.orchestrator.types import PermissionResponse

    safe = _SafeTool()
    executor = ToolExecutor(
        _deps(safe, authorizer=_AskAuthorizer()),
        session_id="s",
        cwd=Path.cwd(),
    )
    executor.add_tool(_tc("1", "SafeRead", id="1", delay=0.05))
    executor.add_tool(_tc("2", "SafeRead", id="2", delay=0.05))
    executor.finalize_stream()

    lock = asyncio.Lock()
    concurrent_calls = 0
    max_concurrent = 0

    async def _tracked_permission(req: Any) -> PermissionResponse:
        nonlocal concurrent_calls, max_concurrent
        if lock.locked():
            max_concurrent = max(max_concurrent, 2)
        async with lock:
            concurrent_calls += 1
            max_concurrent = max(max_concurrent, concurrent_calls)
            await asyncio.sleep(0.02)  # simulate UI round-trip
            concurrent_calls -= 1
        return PermissionResponse(decision="allow")

    events = await _collect_results(executor, on_permission=_tracked_permission)

    # Both tools should have completed (not denied).
    results = [e for e in events if isinstance(e, ToolCallResultEvent)]
    assert len(results) == 2

    # Permission was never called concurrently.
    assert max_concurrent <= 1


@pytest.mark.anyio
async def test_permission_request_carries_authorizer_suggestions() -> None:
    """ToolExecutor forwards dynamic PermissionAsk suggestions to Session."""
    from kernel.orchestrator.types import PermissionResponse

    class _DestructiveAskAuthorizer(_AskAuthorizer):
        async def authorize(
            self, *, tool: Tool, tool_input: dict[str, Any], ctx: AuthorizeContext
        ) -> PermissionAsk:
            return PermissionAsk(
                message=f"approve {tool.name}?",
                decision_reason=ReasonDefaultRisk(
                    risk="high", reason="destructive", tool_name=tool.name
                ),
                suggestions=[
                    PermissionSuggestionBtn(label="Allow once", outcome="allow_once"),
                    PermissionSuggestionBtn(label="Deny", outcome="deny"),
                ],
            )

    safe = _SafeTool()
    executor = ToolExecutor(
        _deps(safe, authorizer=_DestructiveAskAuthorizer()),
        session_id="s",
        cwd=Path.cwd(),
    )
    executor.add_tool(_tc("1", "SafeRead", id="1"))
    executor.finalize_stream()

    seen_options: list[str] = []

    async def _tracked_permission(req: Any) -> PermissionResponse:
        nonlocal seen_options
        seen_options = [option.option_id for option in req.options]
        return PermissionResponse(decision="allow_once")

    events = await _collect_results(executor, on_permission=_tracked_permission)

    assert [e.id for e in events if isinstance(e, ToolCallResultEvent)] == ["1"]
    assert seen_options == ["allow_once", "reject"]
    assert "allow_always" not in seen_options


@pytest.mark.anyio
async def test_max_concurrency() -> None:
    """With max_concurrency=2, at most 2 tools run simultaneously."""
    safe = _SafeTool()
    executor = ToolExecutor(
        _deps(safe),
        session_id="s",
        cwd=Path.cwd(),
        max_concurrency=2,
    )

    # Record concurrent execution count inside the tool.
    concurrent = 0
    max_concurrent = 0
    original_call = _SafeTool.call

    async def _tracking_call(
        self_tool: Any, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        async for item in original_call(self_tool, input, ctx):
            yield item
        concurrent -= 1

    safe.call = lambda input, ctx: _tracking_call(safe, input, ctx)  # type: ignore[assignment]

    for i in range(6):
        executor.add_tool(_tc(str(i), "SafeRead", id=str(i), delay=0.05))
    executor.finalize_stream()

    events = await _collect_results(executor)
    results = [e for e in events if isinstance(e, ToolCallResultEvent)]
    assert len(results) == 6
    assert max_concurrent <= 2


@pytest.mark.anyio
async def test_discard_cancels_inflight() -> None:
    """Calling discard() stops in-flight tools."""
    safe = _SafeTool()
    executor = ToolExecutor(
        _deps(safe), session_id="s", cwd=Path.cwd()
    )
    # One tool with a long delay — we'll discard before it finishes.
    executor.add_tool(_tc("1", "SafeRead", id="1", delay=5.0))
    executor.finalize_stream()

    events: list[Any] = []

    async def _drain() -> None:
        async for event, _result in executor.results(
            on_permission=lambda _: (_ for _ in ()).throw(AssertionError),
            mode="default",
        ):
            events.append(event)

    task = asyncio.create_task(_drain())

    # Let it start, then discard.
    await asyncio.sleep(0.05)
    executor.discard()

    # Should complete quickly after discard.
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail("drain did not complete after discard()")

    # No ToolCallResult should have been produced (tool was cancelled).
    results = [e for e in events if isinstance(e, ToolCallResultEvent)]
    assert len(results) == 0


@pytest.mark.anyio
async def test_backward_compat_run() -> None:
    """The legacy run() method produces the same events as the new interface."""
    safe = _SafeTool()

    # New interface.
    executor_new = ToolExecutor(_deps(safe), session_id="s", cwd=Path.cwd())
    calls = [_tc("1", "SafeRead", id="1"), _tc("2", "SafeRead", id="2")]
    for tc in calls:
        executor_new.add_tool(tc)
    executor_new.finalize_stream()
    new_events = await _collect_results(executor_new)

    # Legacy interface.
    executor_old = ToolExecutor(_deps(safe), session_id="s", cwd=Path.cwd())

    async def _no_perm(req: Any) -> Any:
        raise AssertionError("unexpected")

    old_events = []
    async for event, _result in await executor_old.run(calls, _no_perm, False):
        old_events.append(event)

    # Same event types in same order.
    assert [type(e).__name__ for e in new_events] == [type(e).__name__ for e in old_events]


@pytest.mark.anyio
async def test_finalize_required_before_results() -> None:
    """Calling results() before finalize_stream() raises RuntimeError."""
    executor = ToolExecutor(
        _deps(_SafeTool()), session_id="s", cwd=Path.cwd()
    )
    with pytest.raises(RuntimeError, match="finalize_stream"):
        async for _ in executor.results(
            on_permission=lambda _: (_ for _ in ()).throw(AssertionError),
            mode="default",
        ):
            pass  # pragma: no cover
