"""Tests for _ThinkTagParser — <think> tag extraction from text stream."""

from __future__ import annotations

from daemon.engine.stream import TextDelta, ThinkingDelta
from daemon.providers.openai_compatible import _ThinkTagParser


class TestThinkTagParser:
    """Streaming <think> tag parser tests."""

    def test_plain_text_no_tags(self) -> None:
        """Text without tags passes through as TextDelta."""
        p = _ThinkTagParser()
        events = p.feed("Hello world!")
        assert events == [TextDelta(content="Hello world!")]

    def test_full_think_block(self) -> None:
        """Complete <think>...</think> in one chunk."""
        p = _ThinkTagParser()
        events = p.feed("<think>reasoning here</think>visible text")
        assert events == [
            ThinkingDelta(content="reasoning here"),
            TextDelta(content="visible text"),
        ]

    def test_think_then_text_separate_chunks(self) -> None:
        """Think block and text arrive in separate chunks."""
        p = _ThinkTagParser()
        e1 = p.feed("<think>thinking</think>")
        e2 = p.feed("answer")
        assert e1 == [ThinkingDelta(content="thinking")]
        assert e2 == [TextDelta(content="answer")]

    def test_split_open_tag(self) -> None:
        """Opening tag split across two chunks."""
        p = _ThinkTagParser()
        e1 = p.feed("before<thi")
        e2 = p.feed("nk>inside</think>after")
        # First chunk: "before" emitted, "<thi" buffered
        assert e1 == [TextDelta(content="before")]
        # Second chunk: thinking + text
        assert e2 == [
            ThinkingDelta(content="inside"),
            TextDelta(content="after"),
        ]

    def test_split_close_tag(self) -> None:
        """Closing tag split across two chunks."""
        p = _ThinkTagParser()
        e1 = p.feed("<think>reasoning</thi")
        e2 = p.feed("nk>result")
        # First chunk: inside think, "</thi" buffered
        assert e1 == [ThinkingDelta(content="reasoning")]
        # Second chunk: close tag completed, text emitted
        assert e2 == [TextDelta(content="result")]

    def test_multiple_think_blocks(self) -> None:
        """Multiple think blocks in one stream."""
        p = _ThinkTagParser()
        events = p.feed("<think>a</think>mid<think>b</think>end")
        assert events == [
            ThinkingDelta(content="a"),
            TextDelta(content="mid"),
            ThinkingDelta(content="b"),
            TextDelta(content="end"),
        ]

    def test_think_only_no_text(self) -> None:
        """Entire content is thinking, no visible text."""
        p = _ThinkTagParser()
        events = p.feed("<think>all thinking</think>")
        assert events == [ThinkingDelta(content="all thinking")]

    def test_empty_think_block(self) -> None:
        """Empty think block produces no thinking event."""
        p = _ThinkTagParser()
        events = p.feed("<think></think>text")
        assert events == [TextDelta(content="text")]

    def test_incremental_char_by_char(self) -> None:
        """Feeding character by character still works."""
        p = _ThinkTagParser()
        text = "<think>hi</think>ok"
        all_events = []
        for ch in text:
            all_events.extend(p.feed(ch))

        thinking = [e for e in all_events if isinstance(e, ThinkingDelta)]
        visible = [e for e in all_events if isinstance(e, TextDelta)]
        assert "".join(e.content for e in thinking) == "hi"
        assert "".join(e.content for e in visible) == "ok"

    def test_text_ending_with_angle_bracket(self) -> None:
        """Text ending with '<' that is NOT a tag start."""
        p = _ThinkTagParser()
        e1 = p.feed("a < b")
        # '<' alone is not a prefix of '<think>', should emit
        assert e1 == [TextDelta(content="a < b")]

    def test_no_close_tag(self) -> None:
        """Think block never closed — content emitted as thinking."""
        p = _ThinkTagParser()
        e1 = p.feed("<think>endless thinking ")
        e2 = p.feed("still going")
        assert e1 == [ThinkingDelta(content="endless thinking ")]
        assert e2 == [ThinkingDelta(content="still going")]
