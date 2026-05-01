"""End-to-end tests for sub-agent execution (Phase 5.2).

Verifies the full lifecycle: parent LLM calls ``agent`` tool →
child orchestrator runs → events forwarded → final text returned
as tool_result → parent continues.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from daemon.config.schema import AgentRuntimeConfig
from daemon.engine.orchestrator import Orchestrator
from daemon.engine.orchestrator.agent_factory import AgentFactory
from daemon.engine.stream import (
    AgentEnd,
    AgentStart,
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
from daemon.extensions.tools.builtin.agent_tool import AgentTool
from daemon.extensions.tools.registry import ToolRegistry
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


# ---------------------------------------------------------------------------
# Fake tools
# ---------------------------------------------------------------------------


class FakeReadTool(Tool):
    """Simple read-only tool for the child agent to use."""

    name = "fake_read"
    description = "Read something."
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.PARALLEL

    class Input:
        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            return {"type": "object", "properties": {"query": {"type": "string"}}}

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output=f"result for {params.get('query', '?')}")


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class ParentProvider(Provider):
    """Parent provider: first turn calls agent tool, second turn produces text."""

    name = "parent_prov"

    def __init__(self) -> None:
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
            # Call the agent tool
            yield ToolCallStart(
                tool_call_id="agent_call_1",
                tool_name="agent",
                arguments={
                    "prompt": "Research the topic",
                    "description": "research task",
                },
            )
            yield StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5))
        else:
            # After getting agent result, produce final text
            yield TextDelta(content="Based on the research: all done.")
            yield StreamEnd(usage=UsageInfo(input_tokens=50, output_tokens=10))

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="parent", name="parent", provider="parent_prov")]


class ChildProvider(Provider):
    """Child provider: uses fake_read tool, then produces text.

    Actually the same provider instance is used for both parent and
    child (shared registry).  We use ``ParentProvider`` which adapts
    based on call count.  This class is for documentation only.
    """

    name = "child_prov"


class SimpleChildProvider(Provider):
    """Provider that produces text directly (no tools)."""

    name = "simple_child"

    def __init__(self) -> None:
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
            # Parent: call agent
            yield ToolCallStart(
                tool_call_id="agent_call_1",
                tool_name="agent",
                arguments={"prompt": "What is 2+2?", "description": "math"},
            )
            yield StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5))
        elif self._call_count == 2:
            # Child: answer directly
            yield TextDelta(content="The answer is 4.")
            yield StreamEnd(usage=UsageInfo(input_tokens=5, output_tokens=3))
        else:
            # Parent: final response after agent result
            yield TextDelta(content="The agent said: 4.")
            yield StreamEnd(usage=UsageInfo(input_tokens=20, output_tokens=5))

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="simple", name="simple", provider="simple_child")]


class ToolUsingChildProvider(Provider):
    """Provider where child uses a tool before answering."""

    name = "tool_child"

    def __init__(self) -> None:
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
            # Parent: launch agent
            yield ToolCallStart(
                tool_call_id="agent_1",
                tool_name="agent",
                arguments={"prompt": "Find info about X", "description": "search"},
            )
            yield StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5))
        elif self._call_count == 2:
            # Child: call fake_read tool
            yield ToolCallStart(
                tool_call_id="child_tool_1",
                tool_name="fake_read",
                arguments={"query": "X"},
            )
            yield StreamEnd(usage=UsageInfo(input_tokens=5, output_tokens=3))
        elif self._call_count == 3:
            # Child: produce final text after tool result
            yield TextDelta(content="Found: result for X")
            yield StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=4))
        else:
            # Parent: use agent result
            yield TextDelta(content="Agent found the info.")
            yield StreamEnd(usage=UsageInfo(input_tokens=30, output_tokens=5))

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="tool_child", name="tool_child", provider="tool_child")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orchestrator(
    provider: Provider,
    tmp_path: Any,
    extra_tools: list[Tool] | None = None,
    agent_config: AgentRuntimeConfig | None = None,
) -> Orchestrator:
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    tool_registry = ToolRegistry()
    tool_registry.register(AgentTool())
    for t in extra_tools or []:
        tool_registry.register(t)

    orch = make_test_orchestrator(
        provider=provider,
        tmp_path=tmp_path,
        tool_registry=tool_registry,
    )
    # Pre-set context window to avoid lazy resolution in tests.
    orch.compactor.context_window = 100_000

    # Wire the agent factory
    ac = agent_config or AgentRuntimeConfig(max_depth=3, timeout_seconds=10)
    orch.agent_factory = AgentFactory(orch, ac, depth=0)

    return orch


async def _auto_approve(perm_req: Any) -> Any:
    """Permission callback that auto-approves everything."""
    from daemon.engine.stream import PermissionResponse

    return PermissionResponse(request_id=perm_req.request_id, decision="allow")


async def _collect_events(orch: Orchestrator, text: str) -> list[StreamEvent]:
    events = []
    async for evt in orch.query(text, permission_callback=_auto_approve):
        events.append(evt)
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentExecution:
    """End-to-end sub-agent tests."""

    @pytest.mark.asyncio
    async def test_simple_agent_produces_tool_result(self, tmp_path: Any) -> None:
        """Agent tool call → child runs → tool_result with child's text."""
        orch = _make_orchestrator(SimpleChildProvider(), tmp_path)

        events = await _collect_events(orch, "Ask the agent")

        # Should have AgentStart and AgentEnd
        agent_starts = [e for e in events if isinstance(e, AgentStart)]
        agent_ends = [e for e in events if isinstance(e, AgentEnd)]
        assert len(agent_starts) == 1
        assert len(agent_ends) == 1
        assert agent_starts[0].agent_id == agent_ends[0].agent_id

        # Should have a ToolCallResult for the agent call
        results = [e for e in events if isinstance(e, ToolCallResult)]
        agent_result = [r for r in results if r.tool_call_id == "agent_call_1"]
        assert len(agent_result) == 1
        assert "4" in agent_result[0].output

        # Parent should have produced final text
        text_deltas = [e for e in events if isinstance(e, TextDelta)]
        full_text = "".join(d.content for d in text_deltas)
        assert "4" in full_text

    @pytest.mark.asyncio
    async def test_child_tool_events_forwarded(self, tmp_path: Any) -> None:
        """Child's tool calls are forwarded as events to the client."""
        orch = _make_orchestrator(ToolUsingChildProvider(), tmp_path, extra_tools=[FakeReadTool()])

        events = await _collect_events(orch, "Search for X")

        # Child's ToolCallStart should be forwarded
        tool_starts = [e for e in events if isinstance(e, ToolCallStart)]
        child_starts = [s for s in tool_starts if s.tool_name == "fake_read"]
        assert len(child_starts) == 1

        # Child's ToolCallResult for fake_read should be forwarded
        results = [e for e in events if isinstance(e, ToolCallResult)]
        child_results = [r for r in results if r.tool_name == "fake_read"]
        assert len(child_results) == 1
        assert "result for X" in child_results[0].output

    @pytest.mark.asyncio
    async def test_agent_start_end_bracket_child_events(self, tmp_path: Any) -> None:
        """AgentStart appears before AgentEnd, and tool_result is between them."""
        orch = _make_orchestrator(SimpleChildProvider(), tmp_path)

        events = await _collect_events(orch, "Go")

        start_idx = next(i for i, e in enumerate(events) if isinstance(e, AgentStart))
        end_idx = next(i for i, e in enumerate(events) if isinstance(e, AgentEnd))
        assert start_idx < end_idx

        # The agent tool_result should be between start and end
        result_indices = [
            i
            for i, e in enumerate(events)
            if isinstance(e, ToolCallResult) and e.tool_call_id == "agent_call_1"
        ]
        assert len(result_indices) == 1
        assert start_idx < result_indices[0] <= end_idx

    @pytest.mark.asyncio
    async def test_max_depth_prevents_agent_tool(self, tmp_path: Any) -> None:
        """At max depth, agent tool is removed from child registry."""
        orch = _make_orchestrator(
            SimpleChildProvider(),
            tmp_path,
            agent_config=AgentRuntimeConfig(max_depth=1),
        )

        events = await _collect_events(orch, "Go")

        # Agent should still work (depth 0 → 1, max is 1)
        agent_starts = [e for e in events if isinstance(e, AgentStart)]
        assert len(agent_starts) == 1

    @pytest.mark.asyncio
    async def test_no_factory_returns_error(self, tmp_path: Any) -> None:
        """If agent factory is not wired, agent tool returns error."""
        orch = _make_orchestrator(SimpleChildProvider(), tmp_path)
        orch.agent_factory = None  # Disable factory

        events = await _collect_events(orch, "Go")

        # The agent tool call should result in an error
        results = [e for e in events if isinstance(e, ToolCallResult)]
        agent_results = [r for r in results if r.tool_call_id == "agent_call_1"]
        assert len(agent_results) == 1
        assert agent_results[0].is_error is True
        assert (
            "max depth" in agent_results[0].output.lower()
            or "not initialized" in agent_results[0].output.lower()
        )

    @pytest.mark.asyncio
    async def test_agent_description_in_start_event(self, tmp_path: Any) -> None:
        """AgentStart contains the description from the tool call."""
        orch = _make_orchestrator(SimpleChildProvider(), tmp_path)

        events = await _collect_events(orch, "Go")

        starts = [e for e in events if isinstance(e, AgentStart)]
        assert starts[0].description == "math"
        assert starts[0].prompt == "What is 2+2?"
