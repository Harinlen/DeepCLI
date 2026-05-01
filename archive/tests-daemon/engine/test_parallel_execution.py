"""Integration tests for Phase 5.3 parallel tool execution.

Verifies that the orchestrator correctly runs PARALLEL tools via
``asyncio.gather`` while keeping SERIAL tools sequential, and that
output event order matches the original tool_calls order.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

import pytest

from daemon.engine.orchestrator import Orchestrator
from daemon.engine.stream import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallResult,
    ToolCallStart,
    UsageInfo,
)
from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.registry import ToolRegistry
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


# ---------------------------------------------------------------------------
# Test tools: slow tools that record execution timing
# ---------------------------------------------------------------------------

_execution_log: list[tuple[str, float, float]] = []
"""(tool_name, start_time, end_time) — global log for timing assertions."""


class SlowReadTool(Tool):
    """PARALLEL tool that sleeps briefly, logging timing."""

    name = "slow_read"
    description = "A slow read tool."
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.PARALLEL

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {"id": {"type": "string"}}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool_id = params.get("id", "?")
        start = time.monotonic()
        await asyncio.sleep(0.05)
        end = time.monotonic()
        _execution_log.append((f"slow_read_{tool_id}", start, end))
        return ToolResult(output=f"read-{tool_id}")


class SlowSerialTool(Tool):
    """SERIAL tool that sleeps briefly, logging timing."""

    name = "slow_serial"
    description = "A slow serial tool."
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.SERIAL

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {"id": {"type": "string"}}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool_id = params.get("id", "?")
        start = time.monotonic()
        await asyncio.sleep(0.05)
        end = time.monotonic()
        _execution_log.append((f"slow_serial_{tool_id}", start, end))
        return ToolResult(output=f"serial-{tool_id}")


# ---------------------------------------------------------------------------
# Provider that returns multiple tool calls in one turn
# ---------------------------------------------------------------------------


class MultiToolProvider(Provider):
    """Provider that emits N tool calls on the first turn, text on the second."""

    name = "multi"

    def __init__(self, tool_calls: list[ToolCallStart]) -> None:
        self._tool_calls = tool_calls
        self._call_count = 0

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        self._call_count += 1
        if self._call_count == 1:
            for tc in self._tool_calls:
                yield tc
            yield StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5))
        else:
            yield TextDelta(content="Done.")
            yield StreamEnd(usage=UsageInfo(input_tokens=20, output_tokens=3))

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="multi", name="multi", provider="multi")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orchestrator(
    provider: Provider,
    tmp_path: Any,
    extra_tools: list[Tool] | None = None,
) -> Orchestrator:
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    tool_registry = ToolRegistry()
    for t in extra_tools or []:
        tool_registry.register(t)

    orch = make_test_orchestrator(
        provider=provider,
        tmp_path=tmp_path,
        tool_registry=tool_registry,
    )
    # Pre-set context window to avoid lazy resolution in tests.
    orch.compactor.context_window = 100_000
    return orch


async def _collect_events(orch: Orchestrator, text: str) -> list[StreamEvent]:
    events = []
    async for evt in orch.query(text):
        events.append(evt)
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParallelExecution:
    """Verify that PARALLEL tools actually run concurrently."""

    @pytest.fixture(autouse=True)
    def _clear_log(self) -> None:
        _execution_log.clear()

    @pytest.mark.asyncio
    async def test_parallel_reads_overlap(self, tmp_path: Any) -> None:
        """Three PARALLEL tools should execute concurrently."""
        tool_calls = [
            ToolCallStart(tool_call_id="c1", tool_name="slow_read", arguments={"id": "a"}),
            ToolCallStart(tool_call_id="c2", tool_name="slow_read", arguments={"id": "b"}),
            ToolCallStart(tool_call_id="c3", tool_name="slow_read", arguments={"id": "c"}),
        ]
        provider = MultiToolProvider(tool_calls)
        orch = _make_orchestrator(provider, tmp_path, [SlowReadTool()])

        start = time.monotonic()
        events = await _collect_events(orch, "read three files")
        elapsed = time.monotonic() - start

        # 3 parallel sleeps of 0.05s should take ~0.05s total, not 0.15s.
        # Use generous bound to avoid flaky tests.
        assert elapsed < 0.12, f"Parallel execution too slow: {elapsed:.3f}s"

        # All three tool results should be present.
        results = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(results) == 3

        # Results should be in original order (c1, c2, c3).
        assert results[0].tool_call_id == "c1"
        assert results[1].tool_call_id == "c2"
        assert results[2].tool_call_id == "c3"

    @pytest.mark.asyncio
    async def test_serial_tools_sequential(self, tmp_path: Any) -> None:
        """SERIAL tools each get their own group — no overlap."""
        tool_calls = [
            ToolCallStart(tool_call_id="s1", tool_name="slow_serial", arguments={"id": "x"}),
            ToolCallStart(tool_call_id="s2", tool_name="slow_serial", arguments={"id": "y"}),
        ]
        provider = MultiToolProvider(tool_calls)
        orch = _make_orchestrator(provider, tmp_path, [SlowSerialTool()])

        await _collect_events(orch, "serial work")

        # Execution log should show no overlap.
        assert len(_execution_log) == 2
        name1, start1, end1 = _execution_log[0]
        name2, start2, end2 = _execution_log[1]
        assert start2 >= end1, "SERIAL tools should not overlap"

    @pytest.mark.asyncio
    async def test_mixed_parallel_and_serial(self, tmp_path: Any) -> None:
        """PARALLEL group runs, then SERIAL runs sequentially after."""
        tool_calls = [
            ToolCallStart(tool_call_id="r1", tool_name="slow_read", arguments={"id": "1"}),
            ToolCallStart(tool_call_id="r2", tool_name="slow_read", arguments={"id": "2"}),
            ToolCallStart(tool_call_id="s1", tool_name="slow_serial", arguments={"id": "3"}),
        ]
        provider = MultiToolProvider(tool_calls)
        orch = _make_orchestrator(provider, tmp_path, [SlowReadTool(), SlowSerialTool()])

        events = await _collect_events(orch, "mixed")

        results = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(results) == 3

        # Order preserved: r1, r2, s1.
        assert results[0].tool_call_id == "r1"
        assert results[1].tool_call_id == "r2"
        assert results[2].tool_call_id == "s1"

        # Serial tool should start after both parallel tools finish.
        parallel_entries = [e for e in _execution_log if e[0].startswith("slow_read")]
        serial_entries = [e for e in _execution_log if e[0].startswith("slow_serial")]
        assert len(parallel_entries) == 2
        assert len(serial_entries) == 1
        parallel_end = max(e[2] for e in parallel_entries)
        serial_start = serial_entries[0][1]
        assert serial_start >= parallel_end - 0.01  # small tolerance

    @pytest.mark.asyncio
    async def test_event_order_preserved(self, tmp_path: Any) -> None:
        """ToolCallResult events are yielded in the same order as tool_calls."""
        tool_calls = [
            ToolCallStart(tool_call_id="a", tool_name="slow_read", arguments={"id": "first"}),
            ToolCallStart(tool_call_id="b", tool_name="slow_read", arguments={"id": "second"}),
            ToolCallStart(tool_call_id="c", tool_name="slow_read", arguments={"id": "third"}),
        ]
        provider = MultiToolProvider(tool_calls)
        orch = _make_orchestrator(provider, tmp_path, [SlowReadTool()])

        events = await _collect_events(orch, "order check")

        results = [e for e in events if isinstance(e, ToolCallResult)]
        ids = [r.tool_call_id for r in results]
        assert ids == ["a", "b", "c"]
