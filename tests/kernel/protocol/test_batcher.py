"""Unit tests for the generic Batcher and the ACP batcher config."""

from __future__ import annotations

import asyncio

import pytest

from kernel.protocol.interfaces.batcher import Batcher


# ---------------------------------------------------------------------------
# Generic Batcher tests
# ---------------------------------------------------------------------------


def _make_str_batcher(collected: list, window_ms: float = 5.0) -> Batcher[str]:
    """Batcher[str] that merges by concatenation."""

    async def send(msg: str) -> None:
        collected.append(msg)

    return Batcher(
        send=send,
        is_mergeable=lambda m: not m.startswith("!"),
        merge=lambda a, b: a + b,
        window_ms=window_ms,
    )


class TestBatcherMerge:
    @pytest.mark.anyio
    async def test_mergeable_messages_coalesced(self) -> None:
        collected: list[str] = []
        async with _make_str_batcher(collected, window_ms=5) as b:
            await b.feed("a")
            await b.feed("b")
            await b.feed("c")
        # All three merged into one send on exit (flush).
        assert collected == ["abc"]

    @pytest.mark.anyio
    async def test_non_mergeable_flushes_buffer(self) -> None:
        collected: list[str] = []
        async with _make_str_batcher(collected, window_ms=50) as b:
            await b.feed("x")
            await b.feed("y")
            await b.feed("!stop")  # non-mergeable: flushes "xy" first
        assert collected == ["xy", "!stop"]

    @pytest.mark.anyio
    async def test_timer_flush(self) -> None:
        collected: list[str] = []
        async with _make_str_batcher(collected, window_ms=10) as b:
            await b.feed("hello")
            await asyncio.sleep(0.05)  # wait past the 10 ms window
        assert collected == ["hello"]

    @pytest.mark.anyio
    async def test_empty_exit_no_send(self) -> None:
        collected: list[str] = []
        async with _make_str_batcher(collected) as _:
            pass
        assert collected == []

    @pytest.mark.anyio
    async def test_explicit_flush(self) -> None:
        collected: list[str] = []
        b = _make_str_batcher(collected, window_ms=100)
        async with b:
            await b.feed("1")
            await b.feed("2")
            await b.flush()
            assert collected == ["12"]
            await b.feed("3")
        assert collected == ["12", "3"]


# ---------------------------------------------------------------------------
# ACP batcher config tests
# ---------------------------------------------------------------------------


class TestAcpBatcher:
    @pytest.mark.anyio
    async def test_agent_message_chunks_merged(self) -> None:
        from kernel.protocol.acp.batching import make_acp_batcher
        from kernel.protocol.acp.schemas.updates import (
            AgentMessageChunk,
            SessionUpdateNotification,
        )
        from kernel.protocol.acp.schemas.content import AcpTextBlock

        collected: list = []

        async def send(n: SessionUpdateNotification) -> None:
            collected.append(n)

        async with make_acp_batcher(send, window_ms=5) as b:
            for text in ["Hello", " ", "world"]:
                notif = SessionUpdateNotification(
                    session_id="s1",
                    update=AgentMessageChunk(content=AcpTextBlock(text=text)),
                )
                await b.feed(notif)

        assert len(collected) == 1
        merged_text = collected[0].update.content.text  # type: ignore
        assert merged_text == "Hello world"

    @pytest.mark.anyio
    async def test_tool_call_not_merged(self) -> None:
        from kernel.protocol.acp.batching import make_acp_batcher
        from kernel.protocol.acp.schemas.updates import (
            AgentMessageChunk,
            SessionUpdateNotification,
            ToolCallStart,
        )
        from kernel.protocol.acp.schemas.content import AcpTextBlock

        collected: list = []

        async def send(n: SessionUpdateNotification) -> None:
            collected.append(n)

        async with make_acp_batcher(send, window_ms=5) as b:
            await b.feed(
                SessionUpdateNotification(
                    session_id="s1",
                    update=AgentMessageChunk(content=AcpTextBlock(text="hi")),
                )
            )
            # Non-mergeable — should flush "hi" first, then send tool_call.
            await b.feed(
                SessionUpdateNotification(
                    session_id="s1",
                    update=ToolCallStart(tool_call_id="t1", title="Read file"),
                )
            )

        assert len(collected) == 2
        assert collected[0].update.session_update == "agent_message_chunk"
        assert collected[1].update.session_update == "tool_call"
