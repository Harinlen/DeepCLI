"""ToolExecutor ↔ HookManager — pre/post tool-use + reminder drain."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kernel.hooks import EVENT_SPECS, HookBlock, HookEvent, HookEventCtx
from kernel.llm.types import ToolUseContent
from kernel.orchestrator.events import ToolCallError
from kernel.orchestrator.events import ToolCallResult as ToolCallResultEvent
from kernel.orchestrator.events import ToolCallStart
from kernel.orchestrator.tool_executor import ToolExecutor
from kernel.orchestrator.types import OrchestratorDeps, ToolKind
from kernel.tool_authz.types import (
    AuthorizeContext,
    PermissionAllow,
    ReasonDefaultRisk,
)
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    ToolCallProgress,
    ToolCallResult,
)


# ---------------------------------------------------------------------------
# Fixtures / test doubles
# ---------------------------------------------------------------------------


class _EchoTool(Tool[dict[str, Any], str]):
    name = "Echo"
    description = "echo"
    kind = ToolKind.read

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

        text = f"echoed: {input.get('text', '')}"
        yield ToolCallResult(
            data={"text": input.get("text")},
            llm_content=[TextBlock(type="text", text=text)],
            display=TextDisplay(text=text),
        )


class _CrashTool(_EchoTool):
    name = "Crash"
    kind = ToolKind.execute

    async def call(
        self, input: dict[str, Any], ctx: Any
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        raise RuntimeError("tool blew up")
        yield  # pragma: no cover — make this an async generator


class _StubAuthorizer:
    """Always-allow authorizer for fire-point focus."""

    def __init__(self) -> None:
        self.grants: list[tuple[str, dict[str, Any]]] = []

    async def authorize(
        self, *, tool: Tool, tool_input: dict[str, Any], ctx: AuthorizeContext
    ) -> PermissionAllow:
        return PermissionAllow(
            decision_reason=ReasonDefaultRisk(risk="low", reason="test", tool_name=tool.name),
        )

    def grant(self, *, tool: Tool, tool_input: dict[str, Any], ctx: AuthorizeContext) -> None:
        self.grants.append((tool.name, tool_input))


class _RecordingHooks:
    """Fire log + per-event scripted handlers."""

    def __init__(self) -> None:
        self.captured: list[HookEventCtx] = []
        self.handlers: dict[HookEvent, list[Any]] = {}

    async def fire(self, ctx: HookEventCtx) -> bool:
        self.captured.append(ctx)
        blocked = False
        for handler in self.handlers.get(ctx.event, []):
            try:
                result = handler(ctx)
                if hasattr(result, "__await__"):
                    await result
            except HookBlock:
                if EVENT_SPECS[ctx.event].can_block:
                    blocked = True
        return blocked


def _stub_tool_source(tool: Tool) -> MagicMock:
    src = MagicMock()
    src.lookup.return_value = tool
    src.file_state.return_value = MagicMock()
    return src


def _deps(
    *,
    tool: Tool,
    hooks: _RecordingHooks | None = None,
    queue_reminders: list[list[str]] | None = None,
) -> OrchestratorDeps:
    provider = MagicMock()

    def _queue(reminders: list[str]) -> None:
        if queue_reminders is not None:
            queue_reminders.append(list(reminders))

    return OrchestratorDeps(
        provider=provider,
        tool_source=_stub_tool_source(tool),
        authorizer=_StubAuthorizer(),
        hooks=hooks,
        queue_reminders=_queue if queue_reminders is not None else None,
    )


def _call(id_: str, name: str, input: dict[str, Any]) -> ToolUseContent:
    return ToolUseContent(id=id_, name=name, input=input)


async def _run(
    executor: ToolExecutor,
    calls: list[ToolUseContent],
) -> list[Any]:
    events: list[Any] = []

    async def _no_permission(req: Any) -> Any:  # pragma: no cover — unused in these tests
        raise AssertionError("on_permission should not fire for always-allow authorizer")

    async for event, _result in await executor.run(calls, _no_permission, False):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# pre_tool_use
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pre_tool_use_fires_before_execute() -> None:
    hooks = _RecordingHooks()
    tool = _EchoTool()
    executor = ToolExecutor(_deps(tool=tool, hooks=hooks), session_id="s", cwd=Path.cwd())
    events = await _run(executor, [_call("1", "Echo", {"text": "hi"})])

    # pre_tool_use fires before ToolCallStart; post_tool_use after ToolCallResult.
    event_types = [type(e).__name__ for e in events]
    assert "ToolCallStart" in event_types
    assert "ToolCallResult" in event_types
    fire_events = [c.event for c in hooks.captured]
    assert fire_events == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE]


@pytest.mark.anyio
async def test_pre_tool_use_block_prevents_execution() -> None:
    hooks = _RecordingHooks()

    def _blocker(ctx: HookEventCtx) -> None:
        raise HookBlock("not this one")

    hooks.handlers[HookEvent.PRE_TOOL_USE] = [_blocker]

    tool = _EchoTool()
    executor = ToolExecutor(_deps(tool=tool, hooks=hooks), session_id="s", cwd=Path.cwd())
    events = await _run(executor, [_call("1", "Echo", {"text": "hi"})])

    # Only ToolCallError — ToolCallStart and ToolCallResult must not appear
    # (tool.call was never invoked).
    assert any(isinstance(e, ToolCallError) for e in events)
    assert not any(isinstance(e, ToolCallStart) for e in events)
    assert not any(isinstance(e, ToolCallResultEvent) for e in events)
    # Only the pre_tool_use fire happened; blocked path short-circuits
    # before post_tool_use.
    assert [c.event for c in hooks.captured] == [HookEvent.PRE_TOOL_USE]


@pytest.mark.anyio
async def test_pre_tool_use_can_rewrite_tool_input() -> None:
    hooks = _RecordingHooks()

    def _rewrite(ctx: HookEventCtx) -> None:
        ctx.tool_input = dict(ctx.tool_input)
        ctx.tool_input["text"] = "rewritten"

    hooks.handlers[HookEvent.PRE_TOOL_USE] = [_rewrite]

    tool = _EchoTool()
    executor = ToolExecutor(_deps(tool=tool, hooks=hooks), session_id="s", cwd=Path.cwd())
    events = await _run(executor, [_call("1", "Echo", {"text": "original"})])

    # The tool received the rewritten input — ToolCallResult text reflects it.
    tool_result = next(e for e in events if isinstance(e, ToolCallResultEvent))
    # llm_content[0].text contains "echoed: rewritten"
    first_block = tool_result.content[0]
    assert "rewritten" in getattr(first_block, "text", "")


# ---------------------------------------------------------------------------
# post_tool_failure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tool_exception_fires_post_tool_failure() -> None:
    hooks = _RecordingHooks()
    executor = ToolExecutor(_deps(tool=_CrashTool(), hooks=hooks), session_id="s", cwd=Path.cwd())
    events = await _run(executor, [_call("1", "Crash", {})])

    assert any(isinstance(e, ToolCallError) for e in events)
    fire_events = [c.event for c in hooks.captured]
    # pre_tool_use first, then post_tool_failure (not post_tool_use).
    assert fire_events == [HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_FAILURE]
    failure_ctx = hooks.captured[-1]
    assert failure_ctx.error_message is not None
    assert "blew up" in failure_ctx.error_message


# ---------------------------------------------------------------------------
# queue_reminders drain
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hook_reminders_are_drained_to_session() -> None:
    hooks = _RecordingHooks()

    def _add_reminder(ctx: HookEventCtx) -> None:
        ctx.messages.append("remember: wear a helmet")

    hooks.handlers[HookEvent.POST_TOOL_USE] = [_add_reminder]

    queued: list[list[str]] = []
    executor = ToolExecutor(
        _deps(tool=_EchoTool(), hooks=hooks, queue_reminders=queued),
        session_id="s",
        cwd=Path.cwd(),
    )
    await _run(executor, [_call("1", "Echo", {"text": "hi"})])

    # The post_tool_use handler appended one reminder; drain captured it.
    assert queued == [["remember: wear a helmet"]]


@pytest.mark.anyio
async def test_hook_reminders_are_silent_without_queue_callback() -> None:
    """If deps.queue_reminders is None, reminders are dropped — no crash."""
    hooks = _RecordingHooks()

    def _add(ctx: HookEventCtx) -> None:
        ctx.messages.append("lost")

    hooks.handlers[HookEvent.POST_TOOL_USE] = [_add]
    executor = ToolExecutor(
        _deps(tool=_EchoTool(), hooks=hooks, queue_reminders=None),
        session_id="s",
        cwd=Path.cwd(),
    )
    # Should not raise despite no drain callback.
    await _run(executor, [_call("1", "Echo", {"text": "hi"})])


# ---------------------------------------------------------------------------
# No HookManager configured — fire is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_hooks_attached_still_works() -> None:
    """deps.hooks = None → ToolExecutor does not call fire()."""
    executor = ToolExecutor(_deps(tool=_EchoTool(), hooks=None), session_id="s", cwd=Path.cwd())
    events = await _run(executor, [_call("1", "Echo", {"text": "hi"})])
    assert any(isinstance(e, ToolCallResultEvent) for e in events)
