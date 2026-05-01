"""Tests for AnthropicProvider stream logic.

Patches the Anthropic SDK client so no real API calls are made.

Key invariants
--------------
- ToolUseChunk emitted at content_block_stop (not during delta)
- UsageChunk emitted once at stream end
- ThoughtChunk emitted for thinking_delta
- Transient errors → StreamError chunk (not raised)
- Auth errors → ProviderError raised
- Empty tool input JSON → input={}
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kernel.llm_provider.anthropic import AnthropicProvider
from kernel.llm_provider.errors import ProviderError
from kernel.llm.types import (
    PromptSection,
    StreamError,
    TextChunk,
    ThoughtChunk,
    ToolUseChunk,
    UsageChunk,
    UserMessage,
    TextContent,
)


# ---------------------------------------------------------------------------
# Helpers — build fake SDK events
# ---------------------------------------------------------------------------


def _ev(type_: str, **kw) -> MagicMock:
    ev = MagicMock()
    ev.type = type_
    for k, v in kw.items():
        setattr(ev, k, v)
    return ev


def _tool_start(index: int, id_: str, name: str) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = id_
    block.name = name
    return _ev("content_block_start", index=index, content_block=block)


def _text_start(index: int = 0) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    return _ev("content_block_start", index=index, content_block=block)


def _text_delta(text: str, index: int = 0) -> MagicMock:
    d = MagicMock()
    d.type = "text_delta"
    d.text = text
    return _ev("content_block_delta", index=index, delta=d)


def _thinking_delta(thinking: str, index: int = 0) -> MagicMock:
    d = MagicMock()
    d.type = "thinking_delta"
    d.thinking = thinking
    return _ev("content_block_delta", index=index, delta=d)


def _json_delta(partial: str, index: int) -> MagicMock:
    d = MagicMock()
    d.type = "input_json_delta"
    d.partial_json = partial
    return _ev("content_block_delta", index=index, delta=d)


def _block_stop(index: int) -> MagicMock:
    return _ev("content_block_stop", index=index)


def _usage(input_=10, output=5, cache_read=0, cache_write=0) -> MagicMock:
    u = MagicMock()
    u.input_tokens = input_
    u.output_tokens = output
    u.cache_read_input_tokens = cache_read
    u.cache_creation_input_tokens = cache_write
    return u


def _final_msg(usage) -> MagicMock:
    m = MagicMock()
    m.usage = usage
    return m


# ---------------------------------------------------------------------------
# FakeStream context manager
# ---------------------------------------------------------------------------


class FakeStream:
    def __init__(self, events: list[Any], final_message: Any) -> None:
        self._events = events
        self._fm = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for ev in self._events:
            yield ev

    async def get_final_message(self):
        return self._fm


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="sk-test", base_url=None)


async def _collect(provider, events, final_msg) -> list:
    # Provider.stream() is a sync method that returns an async generator directly.
    # Do NOT await it — just iterate.
    with patch.object(
        provider._client.messages,
        "stream",
        return_value=FakeStream(events, final_msg),
    ):
        chunks = []
        async for chunk in provider.stream(
            system=[PromptSection(text="sys")],
            messages=[UserMessage([TextContent(text="hi")])],
            tool_schemas=[],
            model_id="claude-opus-4-6",
            temperature=None,
            thinking=False,
            max_tokens=1024,
            prompt_caching=False,
        ):
            chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnthropicProviderStream:
    @pytest.mark.anyio
    async def test_text_chunks_yielded(self):
        events = [_text_start(), _text_delta("Hello"), _text_delta(" world"), _block_stop(0)]
        chunks = await _collect(_provider(), events, _final_msg(_usage()))
        text = [c for c in chunks if isinstance(c, TextChunk)]
        assert [c.content for c in text] == ["Hello", " world"]

    @pytest.mark.anyio
    async def test_tool_use_emitted_at_block_stop_not_during_delta(self):
        events = [
            _tool_start(0, "tu_1", "bash"),
            _json_delta('{"cmd":', 0),
            _json_delta(' "ls"}', 0),
            _block_stop(0),
        ]
        chunks = await _collect(_provider(), events, _final_msg(_usage()))
        tool = [c for c in chunks if isinstance(c, ToolUseChunk)]
        assert len(tool) == 1
        assert tool[0].id == "tu_1"
        assert tool[0].name == "bash"
        assert tool[0].input == {"cmd": "ls"}

    @pytest.mark.anyio
    async def test_no_tool_chunk_without_block_stop(self):
        """Tool buffer never flushed if block_stop is missing."""
        events = [
            _tool_start(0, "tu_1", "bash"),
            _json_delta('{"cmd": "ls"}', 0),
            # intentionally no block_stop
        ]
        chunks = await _collect(_provider(), events, _final_msg(_usage()))
        assert not any(isinstance(c, ToolUseChunk) for c in chunks)

    @pytest.mark.anyio
    async def test_multiple_tool_calls(self):
        events = [
            _tool_start(0, "tu_0", "read"),
            _json_delta('{"path":"a"}', 0),
            _block_stop(0),
            _tool_start(1, "tu_1", "write"),
            _json_delta('{"path":"b"}', 1),
            _block_stop(1),
        ]
        chunks = await _collect(_provider(), events, _final_msg(_usage()))
        tools = [c for c in chunks if isinstance(c, ToolUseChunk)]
        assert len(tools) == 2
        assert tools[0].name == "read"
        assert tools[1].name == "write"

    @pytest.mark.anyio
    async def test_empty_tool_input_defaults_to_empty_dict(self):
        events = [_tool_start(0, "tu_1", "no_args"), _block_stop(0)]
        chunks = await _collect(_provider(), events, _final_msg(_usage()))
        tool = next(c for c in chunks if isinstance(c, ToolUseChunk))
        assert tool.input == {}

    @pytest.mark.anyio
    async def test_usage_emitted_once_at_end(self):
        events = [_text_start(), _text_delta("hi"), _block_stop(0)]
        chunks = await _collect(_provider(), events, _final_msg(_usage(input_=100, output=50)))
        usage = [c for c in chunks if isinstance(c, UsageChunk)]
        assert len(usage) == 1
        assert usage[0].input_tokens == 100
        assert usage[0].output_tokens == 50

    @pytest.mark.anyio
    async def test_usage_cache_tokens(self):
        events = []
        chunks = await _collect(
            _provider(), events, _final_msg(_usage(cache_read=200, cache_write=50))
        )
        u = next(c for c in chunks if isinstance(c, UsageChunk))
        assert u.cache_read_tokens == 200
        assert u.cache_write_tokens == 50

    @pytest.mark.anyio
    async def test_thinking_delta_yields_thought_chunk(self):
        events = [_thinking_delta("I need to reason..."), _block_stop(0)]
        chunks = await _collect(_provider(), events, _final_msg(_usage()))
        thoughts = [c for c in chunks if isinstance(c, ThoughtChunk)]
        assert len(thoughts) >= 1
        assert thoughts[0].content == "I need to reason..."

    @pytest.mark.anyio
    async def test_transient_error_yields_stream_error(self):
        class BadStream:
            async def __aenter__(self):
                raise RuntimeError("connection reset")

            async def __aexit__(self, *_):
                pass

        p = _provider()
        with patch.object(p._client.messages, "stream", return_value=BadStream()):
            chunks = []
            async for chunk in p.stream(
                system=[],
                messages=[],
                tool_schemas=[],
                model_id="m",
                temperature=None,
                thinking=False,
                max_tokens=1024,
                prompt_caching=False,
            ):
                chunks.append(chunk)

        errors = [c for c in chunks if isinstance(c, StreamError)]
        assert len(errors) == 1
        assert "connection reset" in errors[0].message

    @pytest.mark.anyio
    async def test_auth_error_raises_provider_error(self):
        class AuthStream:
            async def __aenter__(self):
                raise RuntimeError("invalid x-api-key")

            async def __aexit__(self, *_):
                pass

        p = _provider()
        with patch.object(p._client.messages, "stream", return_value=AuthStream()):
            with pytest.raises(ProviderError):
                async for _ in p.stream(
                    system=[],
                    messages=[],
                    tool_schemas=[],
                    model_id="m",
                    temperature=None,
                    thinking=False,
                    max_tokens=1024,
                    prompt_caching=False,
                ):
                    pass
