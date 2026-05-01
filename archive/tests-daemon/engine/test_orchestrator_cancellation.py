"""Tests for Orchestrator's cancellation finalizer.

Verify that when a query task is cancelled while a tool is in-flight:

* the cancellation phase is recorded correctly for each stage
  (``permission_wait`` / ``pre_hooks`` / ``executing``);
* a synthetic :class:`ToolCallEntry` is written with
  ``synthetic=True``, ``is_error=True``, matching
  ``cancel_phase``, and the right user-facing message;
* the conversation is left with a matching ``tool_result`` so the
  next query() call does not ship an orphaned ``tool_use`` to the
  provider;
* execution phase yields the verify-state warning message that
  tells the LLM the tool might have partially run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from daemon.engine.orchestrator import Orchestrator
from daemon.engine.stream import (
    PermissionRequest,
    PermissionResponse,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallStart,
)
from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.registry import ToolRegistry
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition
from daemon.sessions.entry import ToolCallEntry


class _SlowPermissionTool(Tool):
    """PROMPT-level tool used to exercise permission_wait phase."""

    name = "slow_tool"
    description = "stub"
    permission_level = PermissionLevel.PROMPT

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="done")


class _BlockingExecuteTool(Tool):
    """NONE-level tool that blocks in execute() until cancelled."""

    name = "blocking"
    description = "stub"
    permission_level = PermissionLevel.NONE

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.started.set()
        await asyncio.sleep(30)  # long enough to be cancelled first
        return ToolResult(output="impossible")


class _FakeProvider(Provider):
    """Emits ``tool_call`` on first call, then plain text on subsequent."""

    name = "fake"

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self._call_count = 0

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(name="fake-model", context_window=8000)]

    async def query_context_window(self, model: str | None = None) -> int | None:
        return 8000

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._call_count += 1
        if self._call_count == 1:
            yield ToolCallStart(
                tool_call_id="tc_cancel",
                tool_name=self._tool_name,
                arguments={},
            )
            yield StreamEnd()
        else:
            yield TextDelta(content="done")
            yield StreamEnd()


def _build_orchestrator(
    tmp_path: Path,
    tool: Tool,
    *,
    on_entry: Any = None,
) -> Orchestrator:
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    return make_test_orchestrator(
        provider=_FakeProvider(tool.name),
        tmp_path=tmp_path,
        tool_registry=tool_registry,
        on_entry=on_entry,
    )


async def _drive_until_cancelled(
    orch: Orchestrator,
    text: str,
    *,
    cancel_when: Any,
    permission_callback: Any = None,
) -> tuple[list[StreamEvent], list[Any]]:
    """Run orch.query() in a task, trigger cancel_when() to cancel it.

    ``cancel_when`` is an awaitable that resolves once the test
    wants to cancel the query task (e.g. ``perm_fut`` for
    permission_wait or ``tool.started.wait()`` for executing).
    """
    events: list[StreamEvent] = []
    entries: list[Any] = []

    async def runner() -> None:
        try:
            async for e in orch.query(text, permission_callback=permission_callback):
                events.append(e)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(runner())
    await cancel_when
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return events, entries


class TestCancellationPermissionWait:
    """Cancel while waiting for a permission callback."""

    @pytest.mark.asyncio
    async def test_synthesizes_permission_wait_entry(self, tmp_path: Path) -> None:
        written: list[Any] = []

        orch = _build_orchestrator(
            tmp_path,
            _SlowPermissionTool(),
            on_entry=written.append,
        )

        perm_request_seen = asyncio.Event()

        async def perm_callback(req: PermissionRequest) -> PermissionResponse:
            # Block indefinitely, signalling the test before we start
            # waiting so it knows when to cancel.
            perm_request_seen.set()
            await asyncio.sleep(30)
            return PermissionResponse(request_id=req.request_id, decision="deny")

        await _drive_until_cancelled(
            orch,
            "please",
            cancel_when=perm_request_seen.wait(),
            permission_callback=perm_callback,
        )

        # Synthetic entry for the orphaned tool_use.
        tool_entries = [e for e in written if isinstance(e, ToolCallEntry)]
        assert len(tool_entries) == 1
        entry = tool_entries[0]
        assert entry.synthetic is True
        assert entry.is_error is True
        assert entry.cancel_phase == "permission_wait"
        assert entry.tool_call_id == "tc_cancel"
        assert "tool was not executed" in entry.output.lower()

        # Conversation has a matching tool_result — no orphan left.
        assert orch.conversation.all_unresolved_tool_calls() == []


class TestCancellationExecuting:
    """Cancel while tool.execute() is running."""

    @pytest.mark.asyncio
    async def test_executing_phase_tagged_and_verify_hint(self, tmp_path: Path) -> None:
        written: list[Any] = []
        tool = _BlockingExecuteTool()

        orch = _build_orchestrator(
            tmp_path,
            tool,
            on_entry=written.append,
        )

        await _drive_until_cancelled(
            orch,
            "go",
            cancel_when=tool.started.wait(),
        )

        tool_entries = [e for e in written if isinstance(e, ToolCallEntry)]
        assert len(tool_entries) == 1
        entry = tool_entries[0]
        assert entry.synthetic is True
        assert entry.cancel_phase == "executing"
        # Message must warn the LLM that state may be inconsistent.
        assert "verify" in entry.output.lower()
        assert "may have" in entry.output.lower()
        assert orch.conversation.all_unresolved_tool_calls() == []


class TestCancellationAfterCompletion:
    """Emit is safe after execute() returns."""

    @pytest.mark.asyncio
    async def test_no_synthetic_entry_for_completed_tool(self, tmp_path: Path) -> None:
        written: list[Any] = []

        class _FastTool(Tool):
            name = "fast"
            description = "stub"
            permission_level = PermissionLevel.NONE

            class Input:
                @classmethod
                def model_json_schema(cls) -> dict[str, Any]:
                    return {"type": "object", "properties": {}}

            async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
                return ToolResult(output="success")

        orch = _build_orchestrator(tmp_path, _FastTool(), on_entry=written.append)

        # Run to completion — no cancellation.
        events: list[StreamEvent] = []
        async for e in orch.query("x"):
            events.append(e)

        tool_entries = [e for e in written if isinstance(e, ToolCallEntry)]
        # The single entry is the real result, not synthetic.
        assert len(tool_entries) == 1
        assert tool_entries[0].synthetic is False
        assert tool_entries[0].output == "success"
        assert orch.tool_executor._in_flight_tools == {}
