"""Tests for hook integration in the orchestrator."""

from __future__ import annotations

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
from daemon.extensions.hooks.base import HookConfig, HookEvent, HookType
from daemon.extensions.hooks.registry import HookRegistry
from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.registry import ToolRegistry
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


# ------------------------------------------------------------------
# Fakes
# ------------------------------------------------------------------


class FakeProvider(Provider):
    """Provider yielding pre-configured events."""

    name = "fake"

    def __init__(
        self,
        events: list[StreamEvent] | None = None,
        events_sequence: list[list[StreamEvent]] | None = None,
    ) -> None:
        self._events = events or [
            TextDelta(content="Hello!"),
            StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5)),
        ]
        self._events_sequence = events_sequence
        self._call_count = 0

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if self._events_sequence:
            idx = min(self._call_count, len(self._events_sequence) - 1)
            events = self._events_sequence[idx]
        else:
            events = self._events
        self._call_count += 1
        for event in events:
            yield event

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake", name="fake", provider="fake")]


class FakeBashTool(Tool):
    """Fake bash tool for hook testing."""

    name = "bash"
    description = "Run shell commands."
    permission_level = PermissionLevel.NONE

    class Input:
        """Command input."""

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            }

    def __init__(self) -> None:
        self.executed_commands: list[str] = []

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Record the command and return success."""
        cmd = params.get("command", "")
        self.executed_commands.append(cmd)
        return ToolResult(output=f"ran: {cmd}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_orchestrator(
    provider: Provider,
    tmp_path: object,
    tool_registry: ToolRegistry | None = None,
    hook_registry: HookRegistry | None = None,
) -> Orchestrator:
    """Create an Orchestrator with given provider and registries."""
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    return make_test_orchestrator(
        provider=provider,
        tmp_path=tmp_path,
        tool_registry=tool_registry,
        hook_registry=hook_registry,
    )


async def _collect_events(
    orch: Orchestrator,
    text: str,
    permission_callback: Any = None,
) -> list[StreamEvent]:
    """Collect all events from a query."""
    events: list[StreamEvent] = []
    async for event in orch.query(text, permission_callback=permission_callback):
        events.append(event)
    return events


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestPreToolUseHookBlock:
    """pre_tool_use hook can block tool execution."""

    @pytest.mark.asyncio
    async def test_hook_blocks_tool(self, tmp_path: object) -> None:
        """A blocking pre_tool_use hook prevents tool execution."""
        bash_tool = FakeBashTool()
        tool_reg = ToolRegistry()
        tool_reg.register(bash_tool)

        hook_reg = HookRegistry()
        hook_reg.register(
            HookConfig(
                event=HookEvent.PRE_TOOL_USE,
                type=HookType.COMMAND,
                if_="Bash(rm *)",
                command="echo BLOCKED && exit 1",
            )
        )

        # Provider returns a tool call for "rm -rf /" then text
        tool_call_events: list[StreamEvent] = [
            ToolCallStart(tool_call_id="tc1", tool_name="bash", arguments={"command": "rm -rf /"}),
            StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5)),
        ]
        text_events: list[StreamEvent] = [
            TextDelta(content="Blocked."),
            StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5)),
        ]

        provider = FakeProvider(events_sequence=[tool_call_events, text_events])
        orch = _make_orchestrator(provider, tmp_path, tool_reg, hook_reg)

        events = await _collect_events(orch, "delete everything")

        # Tool should NOT have been executed
        assert bash_tool.executed_commands == []

        # Should have a ToolCallResult with error
        tool_results = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(tool_results) == 1
        assert tool_results[0].is_error
        assert "BLOCKED" in tool_results[0].output

    @pytest.mark.asyncio
    async def test_non_matching_hook_allows_execution(self, tmp_path: object) -> None:
        """A hook that doesn't match the tool call allows execution."""
        bash_tool = FakeBashTool()
        tool_reg = ToolRegistry()
        tool_reg.register(bash_tool)

        hook_reg = HookRegistry()
        hook_reg.register(
            HookConfig(
                event=HookEvent.PRE_TOOL_USE,
                type=HookType.COMMAND,
                if_="Bash(rm *)",
                command="echo BLOCKED && exit 1",
            )
        )

        # Provider calls "ls" (doesn't match "rm *") then text
        tool_call_events: list[StreamEvent] = [
            ToolCallStart(tool_call_id="tc1", tool_name="bash", arguments={"command": "ls -la"}),
            StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5)),
        ]
        text_events: list[StreamEvent] = [
            TextDelta(content="Done."),
            StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5)),
        ]

        provider = FakeProvider(events_sequence=[tool_call_events, text_events])
        orch = _make_orchestrator(provider, tmp_path, tool_reg, hook_reg)

        await _collect_events(orch, "list files")

        # Tool SHOULD have been executed
        assert bash_tool.executed_commands == ["ls -la"]


class TestPostToolUseHook:
    """post_tool_use hooks fire after execution but don't block."""

    @pytest.mark.asyncio
    async def test_post_hook_fires(self, tmp_path: object) -> None:
        """post_tool_use hook fires but does not block."""
        bash_tool = FakeBashTool()
        tool_reg = ToolRegistry()
        tool_reg.register(bash_tool)

        hook_reg = HookRegistry()
        # Even a failing post_tool_use hook should not block
        hook_reg.register(
            HookConfig(
                event=HookEvent.POST_TOOL_USE,
                type=HookType.COMMAND,
                command="exit 1",
            )
        )

        tool_call_events: list[StreamEvent] = [
            ToolCallStart(tool_call_id="tc1", tool_name="bash", arguments={"command": "echo hi"}),
            StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5)),
        ]
        text_events: list[StreamEvent] = [
            TextDelta(content="Done."),
            StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5)),
        ]

        provider = FakeProvider(events_sequence=[tool_call_events, text_events])
        orch = _make_orchestrator(provider, tmp_path, tool_reg, hook_reg)

        events = await _collect_events(orch, "say hi")

        # Tool should have executed despite post hook failure
        assert bash_tool.executed_commands == ["echo hi"]
        tool_results = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(tool_results) == 1
        assert not tool_results[0].is_error


class TestStopHook:
    """stop hooks fire when the LLM finishes."""

    @pytest.mark.asyncio
    async def test_stop_hook_fires_on_text_response(self, tmp_path: object) -> None:
        """stop hook fires even on simple text-only responses."""
        hook_reg = HookRegistry()
        hook_reg.register(
            HookConfig(
                event=HookEvent.STOP,
                type=HookType.COMMAND,
                command="echo stopped",
            )
        )

        provider = FakeProvider()
        orch = _make_orchestrator(provider, tmp_path, hook_registry=hook_reg)

        # Should not raise / hang — stop hook fires transparently
        events = await _collect_events(orch, "hello")
        assert any(isinstance(e, StreamEnd) for e in events)


class TestNoHooks:
    """Orchestrator works normally when no hooks are configured."""

    @pytest.mark.asyncio
    async def test_no_hook_registry(self, tmp_path: object) -> None:
        """Orchestrator works with default (empty) hook registry."""
        provider = FakeProvider()
        orch = _make_orchestrator(provider, tmp_path)

        events = await _collect_events(orch, "hello")
        assert any(isinstance(e, TextDelta) for e in events)
        assert any(isinstance(e, StreamEnd) for e in events)
