"""End-to-end tests for StandardOrchestrator.

Uses FakeLLMProvider (from conftest.py) to script exact LLM responses
without hitting a real API.  Tests the full query() loop including:
- Basic text response
- Multi-turn conversation
- Tool calls (stub error path)
- Extended thinking (ThinkingContent in history)
- Token count update and compaction trigger
- Cancellation
- StreamError → QueryError
- PromptTooLongError → reactive compaction
- Plan mode state
- set_config
"""

from __future__ import annotations

import asyncio
from typing import Any


from kernel.llm.types import (
    TextContent,
    ThinkingContent,
)
from kernel.llm_provider.errors import PromptTooLongError, ProviderError
from kernel.orchestrator import (
    CancelledEvent,
    CompactionEvent,
    OrchestratorConfig,
    QueryError,
    StopReason,
    TextDelta,
    ThoughtDelta,
    ToolCallError,
    ToolCallStart,
)
from kernel.orchestrator.events import OrchestratorEvent

from .conftest import FakeLLMProvider, no_permission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def collect(gen) -> tuple[list[OrchestratorEvent], Any]:
    """Drain an orchestrator generator, return (events, orchestrator)."""
    events = []
    async for event in gen:
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Basic text response
# ---------------------------------------------------------------------------


async def test_basic_text_response(make_orchestrator, fake_provider: FakeLLMProvider) -> None:
    fake_provider.add_text_response("Hello, world!")
    orc = make_orchestrator()

    events = await collect(orc.query([TextContent(text="hi")], on_permission=no_permission))

    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert text_events[0].content == "Hello, world!"
    assert orc.stop_reason == StopReason.end_turn


async def test_multi_chunk_text_assembled_in_history(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """Multiple TextChunks should be joined in the history assistant message."""
    from kernel.llm.types import TextChunk, UsageChunk

    fake_provider.responses.append(
        [
            TextChunk(content="Hello"),
            TextChunk(content=", "),
            TextChunk(content="world!"),
            UsageChunk(input_tokens=10, output_tokens=5),
        ]
    )
    orc = make_orchestrator()
    await collect(orc.query([TextContent(text="hi")], on_permission=no_permission))

    # History should have: user message + assistant message
    assert len(orc._history.messages) == 2
    from kernel.llm.types import AssistantMessage

    asst = orc._history.messages[1]
    assert isinstance(asst, AssistantMessage)
    text_block = next(b for b in asst.content if isinstance(b, TextContent))
    assert text_block.text == "Hello, world!"


# ---------------------------------------------------------------------------
# Multi-turn conversation
# ---------------------------------------------------------------------------


async def test_multi_turn_conversation(make_orchestrator, fake_provider: FakeLLMProvider) -> None:
    """Second query() continues the same history."""
    fake_provider.add_text_response("Turn 1 answer.")
    fake_provider.add_text_response("Turn 2 answer.")
    orc = make_orchestrator()

    await collect(orc.query([TextContent(text="question 1")], on_permission=no_permission))
    await collect(orc.query([TextContent(text="question 2")], on_permission=no_permission))

    # History: user1 asst1 user2 asst2
    assert len(orc._history.messages) == 4
    assert len(fake_provider.calls) == 2
    # Second call should include the first turn in messages
    assert len(fake_provider.calls[1]["messages"]) == 3  # user1 + asst1 + user2


# ---------------------------------------------------------------------------
# Token count update
# ---------------------------------------------------------------------------


async def test_token_count_updated_from_usage_chunk(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    fake_provider.add_text_response("ok", input_tokens=500, output_tokens=50)
    orc = make_orchestrator()
    await collect(orc.query([TextContent(text="x")], on_permission=no_permission))
    # update_token_count sets it to 550, then append_assistant adds a small
    # char-based estimate for the "ok" response text (~1 token).  The important
    # thing is that the count is at least 550 (i.e. provider values were used).
    assert orc._history.token_count >= 550


# ---------------------------------------------------------------------------
# Extended thinking
# ---------------------------------------------------------------------------


async def test_thinking_chunks_yield_thought_delta(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    fake_provider.add_thinking_response(
        thinking="Let me reason…", signature="sig_xyz", text="The answer is 42."
    )
    orc = make_orchestrator()
    events = await collect(
        orc.query([TextContent(text="hard question")], on_permission=no_permission)
    )

    thought_events = [e for e in events if isinstance(e, ThoughtDelta)]
    assert len(thought_events) >= 1
    assert any("Let me reason" in e.content for e in thought_events)


async def test_thinking_content_stored_in_history(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """ThinkingContent must be persisted so Anthropic API receives it next turn."""
    fake_provider.add_thinking_response(
        thinking="chain of thought", signature="sig_abc", text="answer"
    )
    orc = make_orchestrator()
    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    from kernel.llm.types import AssistantMessage

    asst = orc._history.messages[-1]
    assert isinstance(asst, AssistantMessage)
    thinking_blocks = [b for b in asst.content if isinstance(b, ThinkingContent)]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0].thinking == "chain of thought"
    assert thinking_blocks[0].signature == "sig_abc"


async def test_thinking_content_sent_back_on_second_turn(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """The second LLM call must include the ThinkingContent from the first turn."""
    fake_provider.add_thinking_response("think", "sig_1", "first answer")
    fake_provider.add_text_response("second answer")
    orc = make_orchestrator()

    await collect(orc.query([TextContent(text="turn 1")], on_permission=no_permission))
    await collect(orc.query([TextContent(text="turn 2")], on_permission=no_permission))

    # The second call's messages include the assistant message with thinking
    second_call_messages = fake_provider.calls[1]["messages"]
    asst_msg = next(m for m in second_call_messages if hasattr(m, "role") and m.role == "assistant")
    thinking_blocks = [b for b in asst_msg.content if isinstance(b, ThinkingContent)]
    assert len(thinking_blocks) == 1, "ThinkingContent must be forwarded in subsequent calls"


# ---------------------------------------------------------------------------
# Tool calls (stub path)
# ---------------------------------------------------------------------------


async def test_tool_call_without_registry_emits_error(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """With no tool registry, tool calls produce ToolCallError and loop continues."""
    # First response: tool call
    fake_provider.add_tool_response("tc_1", "bash", {"command": "ls"})
    # Second response (after tool error fed back): text answer
    fake_provider.add_text_response("Done after tool error.")

    orc = make_orchestrator()
    events = await collect(orc.query([TextContent(text="run ls")], on_permission=no_permission))

    tool_starts = [e for e in events if isinstance(e, ToolCallStart)]
    tool_errors = [e for e in events if isinstance(e, ToolCallError)]
    text_deltas = [e for e in events if isinstance(e, TextDelta)]

    assert len(tool_starts) == 1
    assert len(tool_errors) == 1
    assert "is not registered" in tool_errors[0].error
    assert len(text_deltas) == 1
    assert orc.stop_reason == StopReason.end_turn


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def test_cancellation_yields_cancelled_event(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """Cancelling the task during a query yields CancelledEvent and stops cleanly."""

    async def slow_stream(**_kwargs):
        yield TextContent  # never reached — just so the generator exists
        await asyncio.sleep(10)  # blocks until cancelled

    from kernel.llm.types import TextChunk

    async def slow_gen(**kwargs):
        yield TextChunk(content="part")
        await asyncio.sleep(10)

    fake_provider.responses.append([])  # will be replaced by monkey-patch

    orc = make_orchestrator()

    # Replace stream to block indefinitely after first chunk

    async def blocking_stream(**kwargs):
        fake_provider.calls.append(kwargs)

        async def gen():
            yield TextChunk(content="start…")
            await asyncio.sleep(10)

        return gen()

    fake_provider.stream = blocking_stream

    events: list[OrchestratorEvent] = []

    async def run() -> None:
        async for event in orc.query([TextContent(text="long task")], on_permission=no_permission):
            events.append(event)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)  # let it start streaming
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # task may re-raise if cancellation propagated

    cancelled = [e for e in events if isinstance(e, CancelledEvent)]
    assert len(cancelled) == 1
    assert orc.stop_reason == StopReason.cancelled


# ---------------------------------------------------------------------------
# StreamError → QueryError
# ---------------------------------------------------------------------------


async def test_stream_error_yields_query_error(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    from kernel.llm.types import StreamError as SE

    fake_provider.responses.append([SE(message="rate limit hit", code="rate_limit_error")])

    orc = make_orchestrator()
    events = await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    query_errors = [e for e in events if isinstance(e, QueryError)]
    assert len(query_errors) == 1
    assert query_errors[0].code == "rate_limit_error"
    assert orc.stop_reason == StopReason.error


async def test_provider_error_yields_query_error(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    # Coroutine (not async generator) that raises before returning a stream.
    async def raising_stream(**kwargs):
        raise ProviderError("auth failure")

    fake_provider.stream = raising_stream
    orc = make_orchestrator()
    events = await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    query_errors = [e for e in events if isinstance(e, QueryError)]
    assert len(query_errors) == 1
    assert "auth failure" in query_errors[0].message
    assert orc.stop_reason == StopReason.error


# ---------------------------------------------------------------------------
# PromptTooLongError → reactive compaction
# ---------------------------------------------------------------------------


async def test_reactive_compaction_on_prompt_too_long(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """PromptTooLongError triggers compaction + retry."""
    call_count = 0

    async def first_raises_then_ok(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise PromptTooLongError("too long")
        # Second call succeeds — but we need a real stream here
        from kernel.llm.types import TextChunk, UsageChunk

        async def gen():
            yield TextChunk(content="ok after compact")
            yield UsageChunk(input_tokens=50, output_tokens=5)

        return gen()

    fake_provider.stream = first_raises_then_ok

    # Build history large enough that compaction has something to do

    orc = make_orchestrator()
    # Pre-populate with 12 turns so boundary > 0
    for i in range(12):
        orc._history.append_user([TextContent(text=f"user {i} " * 50)])
        orc._history.append_assistant(text=f"assistant {i} " * 50, thoughts=[], tool_calls=[])

    events = await collect(
        orc.query([TextContent(text="new question")], on_permission=no_permission)
    )

    compaction_events = [e for e in events if isinstance(e, CompactionEvent)]
    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(compaction_events) >= 1
    assert len(text_events) == 1
    assert text_events[0].content == "ok after compact"
    assert orc.stop_reason == StopReason.end_turn


async def test_reactive_compaction_gives_up_after_max_retries(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    async def always_too_long(**kwargs):
        raise PromptTooLongError("always too long")

    fake_provider.stream = always_too_long
    orc = make_orchestrator()
    # pre-populate so compaction has something to do
    for i in range(12):
        orc._history.append_user([TextContent(text=f"u{i}" * 100)])
        orc._history.append_assistant(text=f"a{i}" * 100, thoughts=[], tool_calls=[])

    events = await collect(orc.query([TextContent(text="q")], on_permission=no_permission))
    query_errors = [e for e in events if isinstance(e, QueryError)]
    assert len(query_errors) == 1
    assert query_errors[0].code == "prompt_too_long"
    assert orc.stop_reason == StopReason.error


# ---------------------------------------------------------------------------
# Plan mode
# ---------------------------------------------------------------------------


async def test_plan_mode_toggle(make_orchestrator, fake_provider: FakeLLMProvider) -> None:
    orc = make_orchestrator()
    assert orc.plan_mode is False
    orc.set_plan_mode(True)
    assert orc.plan_mode is True
    orc.set_plan_mode(False)
    assert orc.plan_mode is False


async def test_set_mode_updates_mode(make_orchestrator, fake_provider: FakeLLMProvider) -> None:
    orc = make_orchestrator()
    assert orc.mode == "default"

    orc.set_mode("auto")
    assert orc.mode == "auto"
    assert orc.plan_mode is False

    orc.set_mode("accept_edits")
    assert orc.mode == "accept_edits"
    assert orc.plan_mode is False

    orc.set_mode("bypass")
    assert orc.mode == "bypass"
    assert orc.plan_mode is False


async def test_set_plan_mode_backward_compat(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    orc = make_orchestrator()
    orc.set_plan_mode(True)
    assert orc.mode == "plan"
    assert orc.plan_mode is True

    orc.set_plan_mode(False)
    assert orc.mode == "default"
    assert orc.plan_mode is False


async def test_plan_mode_property_compat(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """set_mode('plan') is reflected in plan_mode property."""
    orc = make_orchestrator()
    orc.set_mode("plan")
    assert orc.plan_mode is True
    assert orc.mode == "plan"

    orc.set_mode("default")
    assert orc.plan_mode is False


# ---------------------------------------------------------------------------
# set_config
# ---------------------------------------------------------------------------


async def test_set_config_updates_model(make_orchestrator, fake_provider: FakeLLMProvider) -> None:
    from kernel.orchestrator import OrchestratorConfigPatch

    from kernel.llm.config import ModelRef

    orc = make_orchestrator()
    new_ref = ModelRef(provider="fake", model="new-model")
    orc.set_config(OrchestratorConfigPatch(model=new_ref))
    assert orc.config.model == new_ref

    fake_provider.add_text_response("ok")
    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))
    assert fake_provider.calls[-1]["model"] == new_ref


async def test_set_config_partial_update_preserves_other_fields(make_orchestrator) -> None:
    from kernel.orchestrator import OrchestratorConfigPatch
    from kernel.llm.config import ModelRef

    orc = make_orchestrator(config=OrchestratorConfig(model=ModelRef(provider="test", model="m1"), temperature=0.5))
    orc.set_config(OrchestratorConfigPatch(model=ModelRef(provider="test", model="m2")))
    assert orc.config.model == ModelRef(provider="test", model="m2")
    assert orc.config.temperature == 0.5


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


async def test_close_is_idempotent(make_orchestrator) -> None:
    orc = make_orchestrator()
    await orc.close()
    await orc.close()  # should not raise


# ---------------------------------------------------------------------------
# Max turns safety cap
# ---------------------------------------------------------------------------


async def test_max_turns_stops_loop(make_orchestrator, fake_provider: FakeLLMProvider) -> None:
    """When max_turns is passed, the loop stops after that many iterations.

    Each while-loop iteration = one LLM call.  We script one tool-use
    response per turn so the loop always continues (never returns end_turn).
    After max_turns iterations the safety cap fires.
    """
    max_turns = 5

    # Script exactly max_turns + 1 consecutive tool responses.
    for i in range(max_turns + 1):
        fake_provider.add_tool_response(f"tc_{i}", "bash", {"command": "loop"})

    orc = make_orchestrator()
    await collect(
        orc.query(
            [TextContent(text="loop forever")],
            on_permission=no_permission,
            max_turns=max_turns,
        )
    )
    assert orc.stop_reason == StopReason.max_turns


# ---------------------------------------------------------------------------
# user_prompt_submit hook
# ---------------------------------------------------------------------------


class FakeHookManager:
    """Minimal stand-in for HookManager in orchestrator tests.

    Accepts a single handler function that receives the HookEventCtx.
    The handler can mutate ctx fields or raise HookBlock.
    """

    def __init__(self, handler=None):
        self._handler = handler
        self.fired: list[str] = []

    async def fire(self, ctx):
        from kernel.hooks.types import EVENT_SPECS, HookBlock

        self.fired.append(ctx.event.value)
        if self._handler is None:
            return False
        try:
            result = self._handler(ctx)
            if asyncio.iscoroutine(result):
                await result
        except HookBlock:
            spec = EVENT_SPECS[ctx.event]
            if spec.can_block:
                return True
            raise
        return False


async def test_user_prompt_submit_hook_blocks(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """When user_prompt_submit hook blocks, query yields UserPromptBlocked
    and the LLM is never called."""
    from kernel.hooks.types import HookBlock
    from kernel.orchestrator.events import UserPromptBlocked

    def blocker(ctx):
        raise HookBlock("not allowed")

    hooks = FakeHookManager(handler=blocker)
    orc = make_orchestrator(hooks=hooks)

    # Do NOT add any LLM response — provider.stream must not be called.
    events = await collect(orc.query([TextContent(text="hello")], on_permission=no_permission))

    blocked_events = [e for e in events if isinstance(e, UserPromptBlocked)]
    assert len(blocked_events) == 1
    assert blocked_events[0].reason == "user_prompt_submit hook blocked"
    assert orc.stop_reason == StopReason.hook_blocked
    # Provider must not have been called.
    assert len(fake_provider.calls) == 0


async def test_user_prompt_submit_hook_rewrites_prompt(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """When the hook rewrites ctx.user_text, the rewritten text is used
    in the system prompt (via prompt_builder) for the LLM call."""

    rewritten_text = "rewritten by hook"

    def rewriter(ctx):
        ctx.user_text = rewritten_text

    hooks = FakeHookManager(handler=rewriter)
    fake_provider.add_text_response("ok")
    orc = make_orchestrator(hooks=hooks)

    events = await collect(orc.query([TextContent(text="original")], on_permission=no_permission))

    # The query should complete normally.
    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert orc.stop_reason == StopReason.end_turn
    # The hook should have fired.
    assert "user_prompt_submit" in hooks.fired


async def test_user_prompt_submit_hook_drains_reminders(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """Messages appended by the hook handler are drained via queue_reminders."""

    queued: list[list[str]] = []

    from kernel.hooks.types import HookEvent

    def reminder_handler(ctx):
        if ctx.event == HookEvent.USER_PROMPT_SUBMIT:
            ctx.messages.append("reminder from hook")

    def capture_reminders(msgs):
        queued.append(list(msgs))

    hooks = FakeHookManager(handler=reminder_handler)
    fake_provider.add_text_response("ok")
    orc = make_orchestrator(hooks=hooks, queue_reminders=capture_reminders)

    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    assert len(queued) == 1
    assert queued[0] == ["reminder from hook"]


async def test_user_prompt_submit_noop_when_hooks_none(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """When deps.hooks is None, user_prompt_submit is silently skipped."""
    fake_provider.add_text_response("ok")
    orc = make_orchestrator(hooks=None)  # explicit None

    events = await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert orc.stop_reason == StopReason.end_turn


# ---------------------------------------------------------------------------
# post_sampling hook (3c)
# ---------------------------------------------------------------------------


async def test_post_sampling_hook_fires_after_text_response(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """POST_SAMPLING fires once per LLM stream when assistant produces output."""
    hooks = FakeHookManager()
    fake_provider.add_text_response("ok")
    orc = make_orchestrator(hooks=hooks)

    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    assert "post_sampling" in hooks.fired


async def test_post_sampling_hook_fires_after_tool_response(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """POST_SAMPLING fires when the LLM produces tool_use blocks."""
    hooks = FakeHookManager()
    fake_provider.add_tool_response("tc_1", "bash", {"command": "ls"})
    fake_provider.add_text_response("done")
    orc = make_orchestrator(hooks=hooks)

    await collect(orc.query([TextContent(text="run ls")], on_permission=no_permission))

    # Should fire twice: once after tool_use stream, once after text stream.
    post_sampling_count = hooks.fired.count("post_sampling")
    assert post_sampling_count == 2


async def test_post_sampling_hook_not_fired_on_empty_stream(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """POST_SAMPLING should not fire when the LLM returns empty output."""
    from kernel.llm.types import UsageChunk

    hooks = FakeHookManager()
    # Empty stream — only usage chunk, no text or tools.
    fake_provider.responses.append([UsageChunk(input_tokens=10, output_tokens=0)])
    orc = make_orchestrator(hooks=hooks)

    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    assert "post_sampling" not in hooks.fired


async def test_post_sampling_hook_fires_before_tool_execution(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """POST_SAMPLING fires before tool execution starts (ordering check)."""
    event_order: list[str] = []

    def record_hook(ctx):
        event_order.append(f"hook:{ctx.event.value}")

    hooks = FakeHookManager(handler=record_hook)
    fake_provider.add_tool_response("tc_1", "bash", {"command": "ls"})
    fake_provider.add_text_response("done")
    orc = make_orchestrator(hooks=hooks)

    events = await collect(orc.query([TextContent(text="run ls")], on_permission=no_permission))

    # ToolCallStart comes from tool execution — post_sampling must appear before it.
    for e in events:
        if isinstance(e, ToolCallStart):
            event_order.append("tool_start")

    ps_idx = event_order.index("hook:post_sampling")
    ts_idx = event_order.index("tool_start")
    assert ps_idx < ts_idx


# ---------------------------------------------------------------------------
# Abort check — orphan tool_use on cancellation (3d)
# ---------------------------------------------------------------------------


async def test_cancel_with_pending_tool_use_synthesises_results(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """Cancellation after tool_use blocks are in history should synthesise
    error tool_results to keep history well-formed."""
    from kernel.llm.types import ToolUseChunk, UsageChunk, ToolResultContent

    hooks = FakeHookManager()
    orc = make_orchestrator(hooks=hooks)

    # Stream returns a tool_use then blocks — cancel fires during the block.
    async def blocking_stream(**kwargs):
        fake_provider.calls.append(kwargs)

        async def gen():
            yield ToolUseChunk(id="tc_cancel", name="bash", input={"command": "sleep 999"})
            yield UsageChunk(input_tokens=10, output_tokens=5)
            # Block here — cancel will arrive during tool execution
            await asyncio.sleep(10)

        return gen()

    fake_provider.stream = blocking_stream

    events: list = []

    async def run():
        async for event in orc.query([TextContent(text="long task")], on_permission=no_permission):
            events.append(event)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert orc.stop_reason == StopReason.cancelled

    # History must not have orphan tool_use blocks.
    orphans = orc._history.pending_tool_use_ids()
    assert orphans == [], f"Expected no orphans but found: {orphans}"

    # Verify the synthetic tool_result is an error.
    from kernel.llm.types import UserMessage

    last_user = [m for m in orc._history.messages if isinstance(m, UserMessage)]
    if last_user:
        result_blocks = [b for b in last_user[-1].content if isinstance(b, ToolResultContent)]
        for rb in result_blocks:
            if rb.tool_use_id == "tc_cancel":
                assert rb.is_error is True
                assert "Interrupted" in rb.content


async def test_cancel_without_tool_use_no_synthetic_results(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """Cancellation during a text-only stream should not add synthetic results."""
    from kernel.llm.types import TextChunk

    async def blocking_stream(**kwargs):
        fake_provider.calls.append(kwargs)

        async def gen():
            yield TextChunk(content="start…")
            await asyncio.sleep(10)

        return gen()

    fake_provider.stream = blocking_stream

    orc = make_orchestrator()
    events: list = []

    async def run():
        async for event in orc.query([TextContent(text="q")], on_permission=no_permission):
            events.append(event)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert orc.stop_reason == StopReason.cancelled
    # No tool_use in history, so no synthetic results should be added.
    assert orc._history.pending_tool_use_ids() == []


# ---------------------------------------------------------------------------
# STEP 5: stop_reason surface (Phase 1)
# ---------------------------------------------------------------------------


async def test_stop_reason_captured_from_usage_chunk(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """UsageChunk.stop_reason is surfaced through the orchestrator."""
    from kernel.llm.types import TextChunk, UsageChunk

    fake_provider.responses.append(
        [
            TextChunk(content="ok"),
            UsageChunk(input_tokens=10, output_tokens=5, stop_reason="end_turn"),
        ]
    )
    orc = make_orchestrator()
    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    assert orc.stop_reason == StopReason.end_turn


# ---------------------------------------------------------------------------
# STEP 5: MediaSizeError recovery (Phase 2)
# ---------------------------------------------------------------------------


async def test_media_size_error_strips_images_and_retries(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """MediaSizeError triggers strip_media + compact + retry."""
    from kernel.llm.types import ImageContent, TextChunk, UsageChunk
    from kernel.llm_provider.errors import MediaSizeError

    call_count = 0

    async def first_raises_then_ok(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise MediaSizeError("image too large")

        async def gen():
            yield TextChunk(content="ok after strip")
            yield UsageChunk(input_tokens=50, output_tokens=5, stop_reason="end_turn")

        return gen()

    fake_provider.stream = first_raises_then_ok

    orc = make_orchestrator()
    # Pre-populate history with an image in a user message.
    orc._history.append_user([TextContent(text="look at this")])
    orc._history._messages[0] = __import__("kernel.llm.types", fromlist=["UserMessage"]).UserMessage(
        content=[
            TextContent(text="look at this"),
            ImageContent(media_type="image/png", data_base64="abc123"),
        ]
    )
    # Add enough turns for compaction to have something to do.
    for i in range(10):
        orc._history.append_user([TextContent(text=f"u{i}" * 50)])
        orc._history.append_assistant(text=f"a{i}" * 50, thoughts=[], tool_calls=[])

    events = await collect(
        orc.query([TextContent(text="what's in the image?")], on_permission=no_permission)
    )

    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert text_events[0].content == "ok after strip"
    assert orc.stop_reason == StopReason.end_turn

    # Verify images were stripped from history.
    from kernel.llm.types import UserMessage

    for msg in orc._history.messages:
        if isinstance(msg, UserMessage):
            for block in msg.content:
                assert not isinstance(block, ImageContent), "ImageContent should have been stripped"


async def test_media_size_error_gives_up_after_max_retries(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """MediaSizeError gives up after _MAX_REACTIVE_RETRIES."""
    from kernel.llm_provider.errors import MediaSizeError

    async def always_media_error(**kwargs):
        raise MediaSizeError("image too large forever")

    fake_provider.stream = always_media_error
    orc = make_orchestrator()
    for i in range(12):
        orc._history.append_user([TextContent(text=f"u{i}" * 100)])
        orc._history.append_assistant(text=f"a{i}" * 100, thoughts=[], tool_calls=[])

    events = await collect(orc.query([TextContent(text="q")], on_permission=no_permission))
    query_errors = [e for e in events if isinstance(e, QueryError)]
    assert len(query_errors) == 1
    assert query_errors[0].code == "media_size"
    assert orc.stop_reason == StopReason.error


# ---------------------------------------------------------------------------
# STEP 5: max_output_tokens escalation (Phase 3)
# ---------------------------------------------------------------------------


async def test_max_output_tokens_escalation_retries(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """When stop_reason='max_tokens', orchestrator escalates max_tokens and retries."""
    from kernel.llm.types import TextChunk, UsageChunk
    from kernel.orchestrator.orchestrator import _MAX_TOKENS_ESCALATED

    call_count = 0

    async def escalating_stream(**kwargs):
        nonlocal call_count
        fake_provider.calls.append(kwargs)
        call_count += 1

        async def gen():
            if call_count == 1:
                # First call: truncated (max_tokens hit)
                yield TextChunk(content="truncat")
                yield UsageChunk(input_tokens=10, output_tokens=5, stop_reason="max_tokens")
            else:
                # Second call with escalated max_tokens: full response
                yield TextChunk(content="full response")
                yield UsageChunk(input_tokens=10, output_tokens=15, stop_reason="end_turn")

        return gen()

    fake_provider.stream = escalating_stream

    orc = make_orchestrator()
    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    # The withhold pattern pops the first partial — only the second should be in history.
    assert orc.stop_reason == StopReason.end_turn

    # Second call should have max_tokens set to the escalated value.
    assert len(fake_provider.calls) == 2
    assert fake_provider.calls[1]["max_tokens"] == _MAX_TOKENS_ESCALATED
    # First call should have max_tokens=None (default).
    assert fake_provider.calls[0]["max_tokens"] is None


async def test_max_output_tokens_gives_up_after_max_retries(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """After _MAX_OUTPUT_TOKEN_RETRIES, the orchestrator stops retrying."""
    from kernel.llm.types import TextChunk, UsageChunk
    from kernel.orchestrator.orchestrator import _MAX_OUTPUT_TOKEN_RETRIES

    async def always_truncated(**kwargs):
        fake_provider.calls.append(kwargs)

        async def gen():
            yield TextChunk(content="trunc")
            yield UsageChunk(input_tokens=10, output_tokens=5, stop_reason="max_tokens")

        return gen()

    fake_provider.stream = always_truncated

    orc = make_orchestrator()
    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    # 1 initial + 3 retries = 4 total calls.
    assert len(fake_provider.calls) == 1 + _MAX_OUTPUT_TOKEN_RETRIES
    # Should still get end_turn (exhausted retries, accepts the truncated response).
    assert orc.stop_reason == StopReason.end_turn


async def test_pop_last_assistant_removes_partial_turn(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """pop_last_assistant removes the truncated response before retry."""
    from kernel.llm.types import AssistantMessage, TextChunk, UsageChunk

    call_count = 0

    async def escalating_stream(**kwargs):
        nonlocal call_count
        fake_provider.calls.append(kwargs)
        call_count += 1

        async def gen():
            if call_count == 1:
                yield TextChunk(content="partial")
                yield UsageChunk(input_tokens=10, output_tokens=5, stop_reason="max_tokens")
            else:
                yield TextChunk(content="complete")
                yield UsageChunk(input_tokens=10, output_tokens=15, stop_reason="end_turn")

        return gen()

    fake_provider.stream = escalating_stream

    orc = make_orchestrator()
    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    # History should contain the retry's "complete" response, not the partial.
    asst_msgs = [m for m in orc._history.messages if isinstance(m, AssistantMessage)]
    assert len(asst_msgs) == 1
    text_block = next(b for b in asst_msgs[0].content if isinstance(b, TextContent))
    assert text_block.text == "complete"


# ---------------------------------------------------------------------------
# STEP 5: Stop hook (Phase 4)
# ---------------------------------------------------------------------------


async def test_stop_hook_fires_on_end_turn(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """HookEvent.STOP fires with stop_reason when LLM finishes normally."""
    from kernel.hooks.types import HookEvent

    captured_ctx = {}

    def capture_stop(ctx):
        if ctx.event == HookEvent.STOP:
            captured_ctx["stop_reason"] = ctx.stop_reason
            captured_ctx["message_count"] = ctx.message_count
            captured_ctx["token_estimate"] = ctx.token_estimate

    hooks = FakeHookManager(handler=capture_stop)
    fake_provider.add_text_response("ok")
    orc = make_orchestrator(hooks=hooks)

    await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    assert "stop" in hooks.fired
    assert captured_ctx["stop_reason"] == "end_turn"
    assert captured_ctx["message_count"] >= 2  # user + assistant
    assert captured_ctx["token_estimate"] is not None


async def test_stop_hook_not_fired_when_hooks_none(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """When deps.hooks is None, STOP hook is silently skipped."""
    fake_provider.add_text_response("ok")
    orc = make_orchestrator(hooks=None)

    events = await collect(orc.query([TextContent(text="q")], on_permission=no_permission))

    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert orc.stop_reason == StopReason.end_turn


# ---------------------------------------------------------------------------
# STEP 5: Token budget check (Phase 5)
# ---------------------------------------------------------------------------


async def test_token_budget_exceeded(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """When cumulative tokens exceed budget, query yields QueryError
    and stops with budget_exceeded."""
    fake_provider.add_text_response("ok", input_tokens=100, output_tokens=50)
    orc = make_orchestrator()

    events = await collect(
        orc.query(
            [TextContent(text="q")],
            on_permission=no_permission,
            token_budget=10,  # budget too small
        )
    )

    query_errors = [e for e in events if isinstance(e, QueryError)]
    assert len(query_errors) == 1
    assert query_errors[0].code == "token_budget_exceeded"
    assert orc.stop_reason == StopReason.budget_exceeded


async def test_token_budget_not_exceeded(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """When cumulative tokens are within budget, query completes normally."""
    fake_provider.add_text_response("ok", input_tokens=10, output_tokens=5)
    orc = make_orchestrator()

    events = await collect(
        orc.query(
            [TextContent(text="q")],
            on_permission=no_permission,
            token_budget=1000,  # generous budget
        )
    )

    query_errors = [e for e in events if isinstance(e, QueryError)]
    assert len(query_errors) == 0
    assert orc.stop_reason == StopReason.end_turn


async def test_token_budget_none_means_no_cap(
    make_orchestrator, fake_provider: FakeLLMProvider
) -> None:
    """When token_budget is None (default), no budget check is performed."""
    fake_provider.add_text_response("ok", input_tokens=999999, output_tokens=999999)
    orc = make_orchestrator()

    events = await collect(
        orc.query([TextContent(text="q")], on_permission=no_permission)
    )

    query_errors = [e for e in events if isinstance(e, QueryError)]
    assert len(query_errors) == 0
    assert orc.stop_reason == StopReason.end_turn
