"""Tests for the memory auto-extract mixin (Phase 5.7A).

Covers: trigger throttling, coalescing, drain, transcript formatting,
sub-agent skip, and min-messages guard.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest

from daemon.config.defaults import apply_defaults
from daemon.config.schema import (
    MemoryAutoExtractRuntimeConfig,
    SourceConfig,
)
from daemon.engine.memory_extract_prompt import format_extract_prompt
from daemon.engine.orchestrator import Orchestrator
from daemon.engine.orchestrator.agent_factory import AgentFactory
from daemon.engine.orchestrator.memory_extractor import _format_transcript
from daemon.providers.base import (
    ImageContent as ImageContentModel,
    Message as MessageModel,
    TextContent as TextContentModel,
    ToolUseContent as ToolUseModel,
)
from daemon.engine.stream import StreamEnd, StreamEvent, TextDelta, UsageInfo
from daemon.memory.store import MemoryStore
from daemon.providers.base import Message, ModelInfo, Provider, ToolDefinition


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class FakeProvider(Provider):
    """Provider that yields simple text response."""

    name = "fake"

    def __init__(self, text: str = "Hello!") -> None:
        self._text = text

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta(content=self._text)
        yield StreamEnd(usage=UsageInfo(input_tokens=10, output_tokens=5))

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model", name="fake-model", provider="fake")]


def _make_orchestrator(
    tmp_path: Any,
    *,
    memory_store: MemoryStore | None = None,
    auto_extract_cfg: MemoryAutoExtractRuntimeConfig | None = None,
    provider: Provider | None = None,
) -> Orchestrator:
    """Build an orchestrator wired for memory extraction tests."""
    from tests.daemon.engine.orchestrator_test_utils import make_test_orchestrator

    prov = provider or FakeProvider()

    orch = make_test_orchestrator(
        provider=prov,
        tmp_path=tmp_path,
        memory_store=memory_store,
        auto_extract_cfg=auto_extract_cfg,
        session_id="test-session",
    )
    # Wire agent factory (depth 0 = root).
    orch.agent_factory = AgentFactory(orch, orch._config.agent, depth=0)
    return orch


async def _collect_events(orch: Orchestrator, text: str) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for event in orch.query(text):
        events.append(event)
    return events


# ------------------------------------------------------------------
# Tests — format_extract_prompt
# ------------------------------------------------------------------


class TestFormatExtractPrompt:
    """Tests for the extract prompt template."""

    def test_basic_format(self) -> None:
        result = format_extract_prompt("user: hi\nassistant: hello", max_new_memories=5)
        assert "user: hi" in result
        assert "5" in result

    def test_default_max(self) -> None:
        result = format_extract_prompt("test")
        assert "3" in result


# ------------------------------------------------------------------
# Tests — _format_transcript
# ------------------------------------------------------------------


class TestFormatTranscript:
    """Tests for transcript serialisation."""

    def test_simple_messages(self) -> None:
        messages = [
            MessageModel.user("hello"),
            MessageModel.assistant_text("hi there"),
        ]
        result = _format_transcript(messages)
        assert "user: hello" in result
        assert "assistant: hi there" in result

    def test_multi_block_content(self) -> None:
        messages = [
            MessageModel(
                role="assistant",
                content=[
                    TextContentModel(text="Here is the result"),
                    ToolUseModel(tool_call_id="tc1", name="file_read", arguments={}),
                ],
            ),
        ]
        result = _format_transcript(messages)
        assert "Here is the result" in result
        assert "[tool_use: file_read]" in result

    def test_image_block(self) -> None:
        messages = [
            MessageModel(
                role="user",
                content=[ImageContentModel(media_type="image/png", data_base64="abc")],
            ),
        ]
        result = _format_transcript(messages)
        assert "[image]" in result

    def test_empty_messages(self) -> None:
        assert _format_transcript([]) == ""


# ------------------------------------------------------------------
# Tests — trigger logic
# ------------------------------------------------------------------


class TestExtractTrigger:
    """Tests for maybe_trigger_extract throttling and guards."""

    @pytest.mark.asyncio
    async def test_no_trigger_when_disabled(self, tmp_path: Any) -> None:
        """Extract is not triggered when auto_extract.enabled=False."""
        cfg = MemoryAutoExtractRuntimeConfig(enabled=False)
        store = MemoryStore(tmp_path / "mem")
        store.load()
        orch = _make_orchestrator(tmp_path, memory_store=store, auto_extract_cfg=cfg)

        with patch.object(orch.memory_extractor, "_spawn") as mock_spawn:
            orch._maybe_trigger_extract()
            mock_spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_trigger_without_memory_store(self, tmp_path: Any) -> None:
        """Extract is not triggered when memory store is None."""
        orch = _make_orchestrator(tmp_path, memory_store=None)
        # memory_extractor is None when no memory store — just verify no-op.
        orch._maybe_trigger_extract()  # Should not raise.

    @pytest.mark.asyncio
    async def test_no_trigger_in_sub_agent(self, tmp_path: Any) -> None:
        """Extract is not triggered inside sub-agents (depth > 0)."""
        store = MemoryStore(tmp_path / "mem")
        store.load()
        orch = _make_orchestrator(tmp_path, memory_store=store)
        # Simulate sub-agent by setting depth > 0.
        orch.agent_factory = AgentFactory(orch, orch._config.agent, depth=1)

        with patch.object(orch.memory_extractor, "_spawn") as mock_spawn:
            # Satisfy min_messages by adding dummy messages.
            for _ in range(5):
                await orch.conversation.add_user_message("x")
            for _ in range(5):
                orch._maybe_trigger_extract()
            mock_spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_trigger_below_min_messages(self, tmp_path: Any) -> None:
        """Extract is not triggered when conversation has < min_messages."""
        cfg = MemoryAutoExtractRuntimeConfig(min_messages=10, turn_interval=1)
        store = MemoryStore(tmp_path / "mem")
        store.load()
        orch = _make_orchestrator(tmp_path, memory_store=store, auto_extract_cfg=cfg)

        with patch.object(orch.memory_extractor, "_spawn") as mock_spawn:
            # Only 2 messages — below threshold.
            await orch.conversation.add_user_message("a")
            await orch.conversation.add_user_message("b")
            orch._maybe_trigger_extract()
            mock_spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_interval_throttle(self, tmp_path: Any) -> None:
        """Extract only triggers every N turns."""
        cfg = MemoryAutoExtractRuntimeConfig(turn_interval=3, min_messages=1)
        store = MemoryStore(tmp_path / "mem")
        store.load()
        orch = _make_orchestrator(tmp_path, memory_store=store, auto_extract_cfg=cfg)
        await orch.conversation.add_user_message("setup")

        with patch.object(orch.memory_extractor, "_spawn") as mock_spawn:
            # Turns 1, 2 — no trigger.
            orch._maybe_trigger_extract()
            orch._maybe_trigger_extract()
            assert mock_spawn.call_count == 0

            # Turn 3 — trigger.
            orch._maybe_trigger_extract()
            assert mock_spawn.call_count == 1

            # Turns 4, 5 — no trigger.
            orch._maybe_trigger_extract()
            orch._maybe_trigger_extract()
            assert mock_spawn.call_count == 1

            # Turn 6 — trigger again.
            orch._maybe_trigger_extract()
            assert mock_spawn.call_count == 2


# ------------------------------------------------------------------
# Tests — coalescing
# ------------------------------------------------------------------


class TestExtractCoalescing:
    """Tests for concurrent extraction coalescing."""

    @pytest.mark.asyncio
    async def test_coalescing_stashes_when_in_progress(self, tmp_path: Any) -> None:
        """Second spawn while extraction is in-progress stashes context."""
        store = MemoryStore(tmp_path / "mem")
        store.load()
        orch = _make_orchestrator(tmp_path, memory_store=store)
        await orch.conversation.add_user_message("msg1")

        # Simulate in-progress extraction.
        orch.memory_extractor._in_progress = True
        orch.memory_extractor._spawn(
            orch.conversation.get_messages(),
            orch.agent_factory,
        )

        assert orch.memory_extractor._pending_messages is not None
        assert len(orch.memory_extractor._pending_messages) > 0


# ------------------------------------------------------------------
# Tests — drain
# ------------------------------------------------------------------


class TestExtractDrain:
    """Tests for drain_pending_extractions."""

    def _make_orch_with_extractor(self, tmp_path: Any) -> Orchestrator:
        """Build orchestrator with a memory extractor for drain tests."""
        store = MemoryStore(tmp_path / "mem")
        store.load()
        return _make_orchestrator(tmp_path, memory_store=store)

    @pytest.mark.asyncio
    async def test_drain_empty(self, tmp_path: Any) -> None:
        """Drain returns immediately when no extractions are in-flight."""
        orch = self._make_orch_with_extractor(tmp_path)
        await orch.drain_pending_extractions(timeout=1.0)  # Should not hang.

    @pytest.mark.asyncio
    async def test_drain_waits_for_task(self, tmp_path: Any) -> None:
        """Drain awaits in-flight extraction tasks."""
        orch = self._make_orch_with_extractor(tmp_path)
        completed = False

        async def _slow_extract() -> None:
            nonlocal completed
            await asyncio.sleep(0.05)
            completed = True

        task = asyncio.create_task(_slow_extract())
        orch.memory_extractor._in_flight.add(task)
        task.add_done_callback(orch.memory_extractor._in_flight.discard)

        await orch.drain_pending_extractions(timeout=5.0)
        assert completed

    @pytest.mark.asyncio
    async def test_drain_timeout(self, tmp_path: Any) -> None:
        """Drain respects soft timeout without raising."""
        orch = self._make_orch_with_extractor(tmp_path)

        async def _hang() -> None:
            await asyncio.sleep(999)

        task = asyncio.create_task(_hang())
        orch.memory_extractor._in_flight.add(task)

        # Should return after timeout, not hang.
        await orch.drain_pending_extractions(timeout=0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ------------------------------------------------------------------
# Tests — end-to-end integration
# ------------------------------------------------------------------


class TestExtractEndToEnd:
    """End-to-end: query triggers extraction via the real mixin chain."""

    @pytest.mark.asyncio
    async def test_extract_triggered_after_query(self, tmp_path: Any) -> None:
        """After enough queries, maybe_trigger_extract spawns a task."""
        cfg = MemoryAutoExtractRuntimeConfig(
            turn_interval=1,
            min_messages=1,
            timeout=5,
        )
        store = MemoryStore(tmp_path / "mem")
        store.load()
        orch = _make_orchestrator(tmp_path, memory_store=store, auto_extract_cfg=cfg)

        # Patch _run on memory_extractor to track calls without actually running a sub-agent.
        extract_called = asyncio.Event()

        async def _mock_run(messages: Any, factory: Any) -> None:
            extract_called.set()

        with patch.object(orch.memory_extractor, "_run", side_effect=_mock_run):
            # Run a query — this should trigger extract at the end.
            events = await _collect_events(orch, "hello")

            # Wait briefly for the fire-and-forget task.
            try:
                async with asyncio.timeout(2.0):
                    await extract_called.wait()
            except TimeoutError:
                pytest.fail("Extract was not triggered after query")

        # Verify the query itself succeeded.
        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert len(text_events) == 1

    @pytest.mark.asyncio
    async def test_extract_not_triggered_below_interval(self, tmp_path: Any) -> None:
        """Extract is not triggered before turn_interval is reached."""
        cfg = MemoryAutoExtractRuntimeConfig(
            turn_interval=99,  # Very high — should not trigger.
            min_messages=1,
        )
        store = MemoryStore(tmp_path / "mem")
        store.load()
        orch = _make_orchestrator(tmp_path, memory_store=store, auto_extract_cfg=cfg)

        with patch.object(orch.memory_extractor, "_spawn") as mock_spawn:
            await _collect_events(orch, "hello")
            mock_spawn.assert_not_called()


# ------------------------------------------------------------------
# Tests — config schema
# ------------------------------------------------------------------


class TestMemoryConfig:
    """Tests for memory config schema and defaults."""

    def test_default_memory_config(self) -> None:
        """Default config has expected values."""
        config = apply_defaults(SourceConfig())
        assert config.memory.auto_extract.enabled is True
        assert config.memory.auto_extract.turn_interval == 5
        assert config.memory.relevance.threshold == 30
        assert config.memory.hot_cache.top_n == 10

    def test_custom_memory_config(self) -> None:
        """User overrides are respected."""
        from daemon.config.schema import (
            MemoryAutoExtractSourceConfig,
            MemorySourceConfig,
        )

        source = SourceConfig(
            memory=MemorySourceConfig(
                auto_extract=MemoryAutoExtractSourceConfig(
                    enabled=False,
                    turn_interval=10,
                ),
            ),
        )
        config = apply_defaults(source)
        assert config.memory.auto_extract.enabled is False
        assert config.memory.auto_extract.turn_interval == 10
