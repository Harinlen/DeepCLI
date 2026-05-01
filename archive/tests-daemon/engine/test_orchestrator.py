"""Tests for the LLM orchestrator."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from daemon.config.defaults import apply_defaults
from daemon.config.schema import SourceConfig
from daemon.engine.orchestrator import Orchestrator
from daemon.engine.stream import (
    PermissionRequest,
    PermissionResponse,
    StreamEnd,
    StreamError,
    StreamEvent,
    TextDelta,
    ToolCallResult,
    ToolCallStart,
    UsageInfo,
)
from pydantic import BaseModel

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.registry import ToolRegistry
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


# ------------------------------------------------------------------
# Fake provider for testing
# ------------------------------------------------------------------


class FakeProvider(Provider):
    """Provider that yields pre-configured events.

    If ``events_sequence`` is given, yields the i-th list on the i-th
    call to ``stream()``.  Otherwise yields ``events`` every time.
    """

    name = "fake"

    def __init__(
        self,
        events: list[StreamEvent] | None = None,
        events_sequence: list[list[StreamEvent]] | None = None,
    ) -> None:
        self._events = events or [
            TextDelta(content="Hello "),
            TextDelta(content="world!"),
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
        return [ModelInfo(id="fake-model", name="fake-model", provider="fake")]


class ErrorProvider(Provider):
    """Provider that raises ProviderError."""

    name = "error"

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        from daemon.errors import ProviderError

        raise ProviderError("test error")
        yield  # Make it a generator  # pragma: no cover

    async def models(self) -> list[ModelInfo]:
        return []


# ------------------------------------------------------------------
# Fake tools for testing
# ------------------------------------------------------------------


class FakeReadTool(Tool):
    """Tool with NONE permission (auto-approve)."""

    name = "fake_read"
    description = "A fake read tool."
    permission_level = PermissionLevel.NONE

    class Input:
        """No params needed."""

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="read-result")


class FakeWriteTool(Tool):
    """Tool with PROMPT permission (needs confirmation)."""

    name = "fake_write"
    description = "A fake write tool."
    permission_level = PermissionLevel.PROMPT

    class Input:
        """No params needed."""

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="write-result")


class FakeErrorTool(Tool):
    """Tool that always fails."""

    name = "fake_error"
    description = "A tool that errors."
    permission_level = PermissionLevel.NONE

    class Input:
        """No params needed."""

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="something went wrong", is_error=True)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_registry_with_tools(*tools: Tool) -> ToolRegistry:
    """Create a ToolRegistry with given tools."""
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    return registry


def _make_orchestrator(
    provider: Provider,
    tmp_path: object,
    tool_registry: ToolRegistry | None = None,
) -> Orchestrator:
    """Create an Orchestrator with the given fake provider."""
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    return make_test_orchestrator(provider, tmp_path, tool_registry=tool_registry)


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
# Tests — basic chat (no tools)
# ------------------------------------------------------------------


class TestOrchestratorBasicChat:
    """Tests for text-only conversation (no tool calls)."""

    @pytest.mark.asyncio
    async def test_basic_chat(self, tmp_path: object) -> None:
        """Text-only response is streamed and recorded in history."""
        orch = _make_orchestrator(FakeProvider(), tmp_path)
        events = await _collect_events(orch, "hi")

        assert len(events) == 3
        assert isinstance(events[0], TextDelta)
        assert events[0].content == "Hello "
        assert isinstance(events[1], TextDelta)
        assert events[1].content == "world!"
        assert isinstance(events[2], StreamEnd)

    @pytest.mark.asyncio
    async def test_history_recorded(self, tmp_path: object) -> None:
        """User and assistant messages are appended to conversation."""
        orch = _make_orchestrator(FakeProvider(), tmp_path)
        await _collect_events(orch, "hi")

        assert orch.message_count == 2  # user + assistant
        assert orch.conversation.last_assistant_text == "Hello world!"

    @pytest.mark.asyncio
    async def test_provider_error_yields_error_event(self, tmp_path: object) -> None:
        """ProviderError is caught and converted to StreamError."""
        orch = _make_orchestrator(ErrorProvider(), tmp_path)
        events = await _collect_events(orch, "hi")

        assert any(isinstance(e, StreamError) for e in events)
        assert any(isinstance(e, StreamEnd) for e in events)

    @pytest.mark.asyncio
    async def test_clear(self, tmp_path: object) -> None:
        """Clear resets conversation history."""
        orch = _make_orchestrator(FakeProvider(), tmp_path)
        await _collect_events(orch, "hi")
        assert orch.message_count > 0
        await orch.clear()
        assert orch.message_count == 0

    @pytest.mark.asyncio
    async def test_multi_turn(self, tmp_path: object) -> None:
        """Multiple queries accumulate in history."""
        orch = _make_orchestrator(FakeProvider(), tmp_path)
        await _collect_events(orch, "first")
        await _collect_events(orch, "second")
        assert orch.message_count == 4  # 2 user + 2 assistant


# ------------------------------------------------------------------
# Tests — tool execution
# ------------------------------------------------------------------


class TestOrchestratorToolLoop:
    """Tests for the tool execution loop."""

    @pytest.mark.asyncio
    async def test_tool_call_and_result(self, tmp_path: object) -> None:
        """LLM requests a NONE-permission tool → auto-executed → result fed back."""
        tool_reg = _make_registry_with_tools(FakeReadTool())

        # Round 1: LLM calls fake_read
        # Round 2: LLM produces final text
        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(
                        tool_call_id="tc_1",
                        tool_name="fake_read",
                        arguments={},
                    ),
                    StreamEnd(),
                ],
                [
                    TextDelta(content="Done."),
                    StreamEnd(),
                ],
            ]
        )

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        events = await _collect_events(orch, "read something")

        # Expect: ToolCallStart, StreamEnd (round 1), ToolCallResult, TextDelta, StreamEnd (round 2)
        types = [type(e).__name__ for e in events]
        assert "ToolCallStart" in types
        assert "ToolCallResult" in types
        assert "TextDelta" in types

        # Find the ToolCallResult
        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        assert result_events[0].output == "read-result"
        assert result_events[0].is_error is False

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, tmp_path: object) -> None:
        """Unknown tool name → error result fed back to LLM."""
        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(
                        tool_call_id="tc_1",
                        tool_name="nonexistent",
                        arguments={},
                    ),
                    StreamEnd(),
                ],
                [
                    TextDelta(content="Sorry."),
                    StreamEnd(),
                ],
            ]
        )

        orch = _make_orchestrator(provider, tmp_path)
        events = await _collect_events(orch, "do something")

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        assert result_events[0].is_error is True
        assert "Unknown tool" in result_events[0].output

    @pytest.mark.asyncio
    async def test_tool_error_result_fed_back(self, tmp_path: object) -> None:
        """Tool returns is_error=True → error result fed back to LLM."""
        tool_reg = _make_registry_with_tools(FakeErrorTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(
                        tool_call_id="tc_1",
                        tool_name="fake_error",
                        arguments={},
                    ),
                    StreamEnd(),
                ],
                [
                    TextDelta(content="Failed."),
                    StreamEnd(),
                ],
            ]
        )

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        events = await _collect_events(orch, "try something")

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        assert result_events[0].is_error is True


# ------------------------------------------------------------------
# Tests — permissions
# ------------------------------------------------------------------


class TestOrchestratorPermissions:
    """Tests for permission request/response in the tool loop."""

    @pytest.mark.asyncio
    async def test_permission_request_allowed(self, tmp_path: object) -> None:
        """Tool needing permission: allowed → executed."""
        tool_reg = _make_registry_with_tools(FakeWriteTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(
                        tool_call_id="tc_1",
                        tool_name="fake_write",
                        arguments={},
                    ),
                    StreamEnd(),
                ],
                [
                    TextDelta(content="Written."),
                    StreamEnd(),
                ],
            ]
        )

        async def allow_all(req: PermissionRequest) -> PermissionResponse:
            return PermissionResponse(request_id=req.request_id, decision="allow")

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        events = await _collect_events(orch, "write something", permission_callback=allow_all)

        types = [type(e).__name__ for e in events]
        assert "PermissionRequest" in types
        assert "ToolCallResult" in types

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert result_events[0].output == "write-result"
        assert result_events[0].is_error is False

    @pytest.mark.asyncio
    async def test_permission_request_denied(self, tmp_path: object) -> None:
        """Tool needing permission: denied → error result, no execution."""
        tool_reg = _make_registry_with_tools(FakeWriteTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(
                        tool_call_id="tc_1",
                        tool_name="fake_write",
                        arguments={},
                    ),
                    StreamEnd(),
                ],
                [
                    TextDelta(content="Denied."),
                    StreamEnd(),
                ],
            ]
        )

        async def deny_all(req: PermissionRequest) -> PermissionResponse:
            return PermissionResponse(request_id=req.request_id, decision="deny")

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        events = await _collect_events(orch, "write something", permission_callback=deny_all)

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        assert result_events[0].is_error is True
        assert "denied" in result_events[0].output.lower()

    @pytest.mark.asyncio
    async def test_no_callback_denies_permission(self, tmp_path: object) -> None:
        """No permission_callback → permission-requiring tools are denied."""
        tool_reg = _make_registry_with_tools(FakeWriteTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(
                        tool_call_id="tc_1",
                        tool_name="fake_write",
                        arguments={},
                    ),
                    StreamEnd(),
                ],
                [
                    TextDelta(content="No callback."),
                    StreamEnd(),
                ],
            ]
        )

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        events = await _collect_events(orch, "write something")

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert result_events[0].is_error is True
        assert "denied" in result_events[0].output.lower()

    @pytest.mark.asyncio
    async def test_none_level_skips_permission(self, tmp_path: object) -> None:
        """NONE-permission tools don't trigger permission request."""
        tool_reg = _make_registry_with_tools(FakeReadTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(
                        tool_call_id="tc_1",
                        tool_name="fake_read",
                        arguments={},
                    ),
                    StreamEnd(),
                ],
                [
                    TextDelta(content="Done."),
                    StreamEnd(),
                ],
            ]
        )

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        events = await _collect_events(orch, "read something")

        # No PermissionRequest should appear
        assert not any(isinstance(e, PermissionRequest) for e in events)
        # But tool result should
        assert any(isinstance(e, ToolCallResult) for e in events)


# ------------------------------------------------------------------
# Compact integration tests
# ------------------------------------------------------------------


class TestOrchestratorCompact:
    """Tests for auto-compact and force_compact integration."""

    @pytest.mark.asyncio
    async def test_auto_compact_does_not_fire_below_threshold(self, tmp_path: object) -> None:
        """Normal query should not trigger compaction."""
        from daemon.engine.stream import CompactNotification

        provider = FakeProvider()
        orch = _make_orchestrator(provider, tmp_path)
        # Set a large context window so compact never fires
        orch.compactor.context_window = 1_000_000

        events = await _collect_events(orch, "hello")
        assert not any(isinstance(e, CompactNotification) for e in events)

    @pytest.mark.asyncio
    async def test_auto_compact_fires_above_threshold(self, tmp_path: object) -> None:
        """When token count exceeds threshold, compaction should fire."""
        from daemon.engine.stream import CompactNotification

        # Provider that returns summary on the compact call, then normal response.
        # The orchestrator calls provider.stream twice: once for compact, once for query.
        provider = FakeProvider(
            events_sequence=[
                # First call: compact summary
                [
                    TextDelta(content="## 1. User Intent\nSummary"),
                    StreamEnd(usage=UsageInfo(input_tokens=100, output_tokens=50)),
                ],
                # Second call: normal LLM response
                [
                    TextDelta(content="OK"),
                    StreamEnd(usage=UsageInfo(input_tokens=50, output_tokens=10)),
                ],
            ]
        )
        orch = _make_orchestrator(provider, tmp_path)
        # Very small context window to force compaction
        orch.compactor.context_window = 100

        # Seed conversation with enough messages
        for i in range(10):
            await orch.conversation.add_user_message(f"msg {i}" * 50)
            await orch.conversation.add_assistant_text(f"reply {i}" * 50)

        # Fake the token count high enough
        orch.compactor.state.last_known_input_tokens = 200

        events = await _collect_events(orch, "trigger compact")
        assert any(isinstance(e, CompactNotification) for e in events)

    @pytest.mark.asyncio
    async def test_force_compact_too_few_messages(self, tmp_path: object) -> None:
        """force_compact with too few messages yields error."""
        provider = FakeProvider()
        orch = _make_orchestrator(provider, tmp_path)
        orch.compactor.context_window = 1_000_000

        events: list[StreamEvent] = []
        async for event in orch.force_compact():
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], StreamError)
        assert "Not enough" in events[0].message

    @pytest.mark.asyncio
    async def test_force_compact_success(self, tmp_path: object) -> None:
        """force_compact with enough messages should produce a CompactNotification."""
        from daemon.engine.stream import CompactNotification

        provider = FakeProvider(
            events=[
                TextDelta(content="Summary of conversation"),
                StreamEnd(usage=UsageInfo(input_tokens=100, output_tokens=50)),
            ]
        )
        orch = _make_orchestrator(provider, tmp_path)
        orch.compactor.context_window = 1_000_000

        # Seed with enough messages
        for i in range(10):
            await orch.conversation.add_user_message(f"msg {i}")
            await orch.conversation.add_assistant_text(f"reply {i}")

        events: list[StreamEvent] = []
        async for event in orch.force_compact():
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], CompactNotification)
        assert events[0].messages_summarized > 0

    @pytest.mark.asyncio
    async def test_compact_state_tracks_input_tokens(self, tmp_path: object) -> None:
        """StreamEnd usage should update _compact_state.last_known_input_tokens."""
        provider = FakeProvider(
            events=[
                TextDelta(content="hi"),
                StreamEnd(usage=UsageInfo(input_tokens=5000, output_tokens=100)),
            ]
        )
        orch = _make_orchestrator(provider, tmp_path)
        orch.compactor.context_window = 1_000_000

        await _collect_events(orch, "test")
        assert orch.compactor.state.last_known_input_tokens == 5000


# ------------------------------------------------------------------
# Tool result budget tests
# ------------------------------------------------------------------


class TestOrchestratorToolResultBudget:
    """Tests for tool result budget enforcement."""

    @pytest.mark.asyncio
    async def test_large_output_truncated(self, tmp_path: object) -> None:
        """Tool output exceeding max_result_chars is truncated."""
        from pathlib import Path
        from daemon.extensions.tools.result_store import ResultStore

        # Tool that returns large output
        class BigOutputTool(Tool):
            name = "big_tool"
            description = "Returns large output"
            permission_level = PermissionLevel.NONE
            max_result_chars = 100  # Low budget for testing

            class Input(BaseModel):
                pass

            async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
                return ToolResult(output="x" * 500)

        tool_reg = _make_registry_with_tools(BigOutputTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(tool_call_id="tc_1", tool_name="big_tool", arguments={}),
                    StreamEnd(),
                ],
                [TextDelta(content="Done."), StreamEnd()],
            ]
        )

        cache_dir = Path(str(tmp_path)) / "cache"
        result_store = ResultStore(cache_dir)

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        orch.tool_executor._result_store = result_store
        orch.compactor.context_window = 1_000_000

        events = await _collect_events(orch, "do it")

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        # Output should be a truncated summary, not the raw 500 x's
        assert "Output too large" in result_events[0].output
        assert "file_read" in result_events[0].output
        assert result_events[0].output != "x" * 500

    @pytest.mark.asyncio
    async def test_small_output_unchanged(self, tmp_path: object) -> None:
        """Tool output within budget passes through unchanged."""
        from pathlib import Path
        from daemon.extensions.tools.result_store import ResultStore

        tool_reg = _make_registry_with_tools(FakeReadTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(tool_call_id="tc_1", tool_name="fake_read", arguments={}),
                    StreamEnd(),
                ],
                [TextDelta(content="Done."), StreamEnd()],
            ]
        )

        cache_dir = Path(str(tmp_path)) / "cache"
        result_store = ResultStore(cache_dir)

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        orch.tool_executor._result_store = result_store
        orch.compactor.context_window = 1_000_000

        events = await _collect_events(orch, "read it")

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        assert result_events[0].output == "read-result"  # Unchanged

    @pytest.mark.asyncio
    async def test_no_budget_without_result_store(self, tmp_path: object) -> None:
        """Without result_store, no budget enforcement occurs."""

        class BigOutputTool(Tool):
            name = "big_tool"
            description = "Returns large output"
            permission_level = PermissionLevel.NONE
            max_result_chars = 100

            class Input(BaseModel):
                pass

            async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
                return ToolResult(output="x" * 500)

        tool_reg = _make_registry_with_tools(BigOutputTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(tool_call_id="tc_1", tool_name="big_tool", arguments={}),
                    StreamEnd(),
                ],
                [TextDelta(content="Done."), StreamEnd()],
            ]
        )

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        # No result_store set
        orch.compactor.context_window = 1_000_000

        events = await _collect_events(orch, "do it")

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        assert result_events[0].output == "x" * 500  # Full output, no truncation

    @pytest.mark.asyncio
    async def test_error_output_not_budgeted(self, tmp_path: object) -> None:
        """Error outputs skip budget enforcement."""
        from pathlib import Path
        from daemon.extensions.tools.result_store import ResultStore

        class FailTool(Tool):
            name = "fail_tool"
            description = "Always fails"
            permission_level = PermissionLevel.NONE
            max_result_chars = 10  # Very low budget

            class Input(BaseModel):
                pass

            async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
                return ToolResult(output="E" * 500, is_error=True)

        tool_reg = _make_registry_with_tools(FailTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(tool_call_id="tc_1", tool_name="fail_tool", arguments={}),
                    StreamEnd(),
                ],
                [TextDelta(content="Failed."), StreamEnd()],
            ]
        )

        cache_dir = Path(str(tmp_path)) / "cache"
        result_store = ResultStore(cache_dir)

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        orch.tool_executor._result_store = result_store
        orch.compactor.context_window = 1_000_000

        events = await _collect_events(orch, "fail it")

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        # Error output should NOT be truncated
        assert result_events[0].output == "E" * 500
        assert result_events[0].is_error is True

    @pytest.mark.asyncio
    async def test_none_max_result_chars_skips_budget(self, tmp_path: object) -> None:
        """Tool with max_result_chars=None is never truncated."""
        from pathlib import Path
        from daemon.extensions.tools.result_store import ResultStore

        class UnlimitedTool(Tool):
            name = "unlimited"
            description = "No limit"
            permission_level = PermissionLevel.NONE
            max_result_chars = None

            class Input(BaseModel):
                pass

            async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
                return ToolResult(output="z" * 100_000)

        tool_reg = _make_registry_with_tools(UnlimitedTool())

        provider = FakeProvider(
            events_sequence=[
                [
                    ToolCallStart(tool_call_id="tc_1", tool_name="unlimited", arguments={}),
                    StreamEnd(),
                ],
                [TextDelta(content="Done."), StreamEnd()],
            ]
        )

        cache_dir = Path(str(tmp_path)) / "cache"
        result_store = ResultStore(cache_dir)

        orch = _make_orchestrator(provider, tmp_path, tool_registry=tool_reg)
        orch.tool_executor._result_store = result_store
        orch.compactor.context_window = 1_000_000

        events = await _collect_events(orch, "read it all")

        result_events = [e for e in events if isinstance(e, ToolCallResult)]
        assert len(result_events) == 1
        assert result_events[0].output == "z" * 100_000

    @pytest.mark.asyncio
    async def test_context_window_resolved_lazily(self, tmp_path: object) -> None:
        """Context window should be resolved on first query."""
        provider = FakeProvider()
        orch = _make_orchestrator(provider, tmp_path)
        orch.compactor.context_window = 0  # Reset to test lazy resolution.
        assert orch.compactor.context_window == 0

        await _collect_events(orch, "first query")
        # Should be resolved to default (32768) since fake provider returns None
        assert orch.compactor.context_window > 0


class TestProviderOverride:
    """/model switch — session-level provider override."""

    def _multi_provider_orch(self, tmp_path: object) -> tuple[Orchestrator, "Any"]:
        """Build an orchestrator with two fake providers (alpha, beta)."""
        from daemon.config.schema import (
            ProviderRuntimeConfig,
        )
        from daemon.providers.registry import ProviderRegistry

        class NamedProvider(FakeProvider):
            def __init__(self, name: str) -> None:
                super().__init__()
                self.name = name
                self.called_with_model: list[str | None] = []

            async def stream(self, messages, tools=None, model=None, system=None, **kwargs):
                self.called_with_model.append(model)
                for ev in self._events:
                    yield ev

        p_alpha = NamedProvider("alpha")
        p_beta = NamedProvider("beta")

        config = apply_defaults(SourceConfig())
        # Replace providers with our two fakes.
        config.providers = {
            "alpha": ProviderRuntimeConfig(
                type="openai_compatible",
                base_url="http://a",
                model="model-A",
                api_key="x",
            ),
            "beta": ProviderRuntimeConfig(
                type="openai_compatible",
                base_url="http://b",
                model="model-B",
                api_key="y",
            ),
        }
        config.default_provider = "alpha"

        registry = ProviderRegistry()
        registry._default_provider = "alpha"
        registry.register(p_alpha)
        registry.register(p_beta)

        from pathlib import Path

        from daemon.engine.orchestrator.compactor import Compactor
        from daemon.engine.orchestrator.plan_mode import PlanModeController
        from daemon.engine.orchestrator.prompt_builder import SystemPromptBuilder
        from daemon.engine.orchestrator.tool_executor import ToolExecutor
        from daemon.permissions.engine import PermissionEngine
        from daemon.permissions.settings import PermissionSettings

        pe = PermissionEngine(PermissionSettings())
        tool_executor = ToolExecutor(
            permission_engine=pe,
            tool_registry=ToolRegistry(),
        )
        compactor = Compactor(context_window=200_000)
        plan_mode = PlanModeController(pe)
        prompt_builder = SystemPromptBuilder(Path(tmp_path))

        orch = Orchestrator(
            registry=registry,
            config=config,
            tool_executor=tool_executor,
            compactor=compactor,
            plan_mode=plan_mode,
            prompt_builder=prompt_builder,
        )
        return orch, (p_alpha, p_beta)

    def test_effective_provider_name_default(self, tmp_path: object) -> None:
        orch, _ = self._multi_provider_orch(tmp_path)
        assert orch.effective_provider_name == "alpha"
        assert orch.effective_model == "model-A"

    def test_set_provider_override_valid(self, tmp_path: object) -> None:
        orch, _ = self._multi_provider_orch(tmp_path)
        orch.set_provider_override("beta")
        assert orch.effective_provider_name == "beta"
        assert orch.effective_model == "model-B"

    def test_set_provider_override_invalid(self, tmp_path: object) -> None:
        orch, _ = self._multi_provider_orch(tmp_path)
        with pytest.raises(ValueError, match="not configured"):
            orch.set_provider_override("ghost")
        # State unchanged.
        assert orch.effective_provider_name == "alpha"

    def test_set_provider_override_none_clears(self, tmp_path: object) -> None:
        orch, _ = self._multi_provider_orch(tmp_path)
        orch.set_provider_override("beta")
        orch.set_provider_override(None)
        assert orch.effective_provider_name == "alpha"
        assert orch._provider_override is None

    def test_get_provider_snapshot(self, tmp_path: object) -> None:
        orch, _ = self._multi_provider_orch(tmp_path)
        snap = orch.get_provider_snapshot()
        assert snap["current_provider_name"] == "alpha"
        assert snap["current_model"] == "model-A"
        assert snap["is_override"] is False
        assert snap["default_provider_name"] == "alpha"
        names = [p["name"] for p in snap["providers"]]
        assert set(names) == {"alpha", "beta"}

    def test_get_provider_snapshot_override(self, tmp_path: object) -> None:
        orch, _ = self._multi_provider_orch(tmp_path)
        orch.set_provider_override("beta")
        snap = orch.get_provider_snapshot()
        assert snap["current_provider_name"] == "beta"
        assert snap["current_model"] == "model-B"
        assert snap["is_override"] is True

    @pytest.mark.asyncio
    async def test_query_uses_effective_provider(self, tmp_path: object) -> None:
        """After set_provider_override, query() dispatches to the new provider."""
        orch, (p_alpha, p_beta) = self._multi_provider_orch(tmp_path)

        await _collect_events(orch, "q1")
        assert len(p_alpha.called_with_model) == 1
        assert p_alpha.called_with_model[0] == "model-A"
        assert len(p_beta.called_with_model) == 0

        orch.set_provider_override("beta")
        await _collect_events(orch, "q2")
        assert len(p_beta.called_with_model) == 1
        assert p_beta.called_with_model[0] == "model-B"


class TestGitStatusMemoize:
    """Session-level memoize of git status (phase4-batch1 决策 1)."""

    @pytest.mark.asyncio
    async def test_git_status_fetched_once(self, tmp_path: object, monkeypatch: Any) -> None:
        """get_git_status is called only once across multiple queries."""
        call_count = [0]

        async def fake_get(cwd):
            call_count[0] += 1
            return "Current branch: main"

        monkeypatch.setattr("daemon.utils.git.get_git_status", fake_get)

        orch = _make_orchestrator(FakeProvider(), tmp_path)
        await _collect_events(orch, "query 1")
        await _collect_events(orch, "query 2")
        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_git_status_none_cached(self, tmp_path: object, monkeypatch: Any) -> None:
        """Non-git directory (None) is cached, not re-fetched."""
        call_count = [0]

        async def fake_get(cwd):
            call_count[0] += 1
            return None

        monkeypatch.setattr("daemon.utils.git.get_git_status", fake_get)

        orch = _make_orchestrator(FakeProvider(), tmp_path)
        await _collect_events(orch, "q1")
        await _collect_events(orch, "q2")
        assert call_count[0] == 1
        # Cache holds None (sentinel cleared).
        assert orch.prompt_builder._git_status is None

    @pytest.mark.asyncio
    async def test_invalidate_forces_refetch(self, tmp_path: object, monkeypatch: Any) -> None:
        """invalidate_git_status() causes the next query to re-fetch."""
        call_count = [0]

        async def fake_get(cwd):
            call_count[0] += 1
            return f"status-{call_count[0]}"

        monkeypatch.setattr("daemon.utils.git.get_git_status", fake_get)

        orch = _make_orchestrator(FakeProvider(), tmp_path)
        await _collect_events(orch, "q1")
        assert call_count[0] == 1
        assert orch.prompt_builder._git_status == "status-1"

        orch.invalidate_git_status()
        assert orch.prompt_builder._git_status is orch.prompt_builder.GIT_STATUS_UNSET

        await _collect_events(orch, "q2")
        assert call_count[0] == 2
        assert orch.prompt_builder._git_status == "status-2"

    @pytest.mark.asyncio
    async def test_git_status_injected_into_system_prompt(
        self, tmp_path: object, monkeypatch: Any
    ) -> None:
        """Fetched git status reaches build_system_prompt via provider.stream."""
        from daemon.engine.context import prompt_sections_to_text

        captured_system: list[str] = []

        class CapturingProvider(FakeProvider):
            name = "capture"

            async def stream(self, messages, tools=None, model=None, system=None, **kwargs):
                text = prompt_sections_to_text(system) if system else ""
                captured_system.append(text)
                for ev in self._events:
                    yield ev

        async def fake_get(cwd):
            return "Current branch: test-branch"

        monkeypatch.setattr("daemon.utils.git.get_git_status", fake_get)

        orch = _make_orchestrator(CapturingProvider(), tmp_path)
        await _collect_events(orch, "hi")
        assert any("# Git Context" in s for s in captured_system)
        assert any("test-branch" in s for s in captured_system)


# ------------------------------------------------------------------
# Tests — orphaned tool_use recovery (client reconnect + resend)
# ------------------------------------------------------------------


class TestOrchestratorOrphanedToolCall:
    """Regression: query() strips orphaned tool_use before resending.

    Scenario: user sent a message, the LLM requested a tool, the
    client disconnected mid-permission-prompt, the query task was
    cancelled.  On reconnect the in-memory session's conversation
    holds an assistant message with ``tool_use`` but no matching
    ``tool_result``.  The client resends the same user message —
    query() must strip the orphan before calling the provider,
    otherwise OpenAI/Anthropic rejects the message sequence.
    """

    @pytest.mark.asyncio
    async def test_strips_orphaned_tool_use_on_resend(self, tmp_path: object) -> None:
        """Orphaned tool_use is stripped before the LLM sees the history."""
        captured: list[list[Any]] = []

        class CapturingProvider(FakeProvider):
            name = "capture"

            async def stream(self, messages, tools=None, model=None, system=None, **kwargs):
                captured.append(list(messages))
                for ev in self._events:
                    yield ev

        orch = _make_orchestrator(
            CapturingProvider(events=[TextDelta(content="ok"), StreamEnd()]),
            tmp_path,
        )
        # Simulate a cancelled query: user + assistant(tool_use), no result.
        from daemon.providers.base import ToolUseContent

        await orch.conversation.add_user_message("check git status")
        await orch.conversation.add_assistant_message(
            [
                ToolUseContent(
                    tool_call_id="orphan",
                    name="bash",
                    arguments={"command": "git status"},
                ),
            ]
        )
        assert len(orch.conversation.pending_tool_calls) == 1

        # Client reconnects and resends the user message.
        await _collect_events(orch, "check git status")

        # Provider must have been called without the orphaned tool_use.
        assert captured, "provider.stream was not called"
        msgs = captured[0]
        for m in msgs:
            if m.role == "assistant":
                for c in m.content:
                    assert not isinstance(c, ToolUseContent), "orphaned tool_use leaked to provider"
        assert orch.conversation.pending_tool_calls == []
