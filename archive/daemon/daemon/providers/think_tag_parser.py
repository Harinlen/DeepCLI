"""Streaming parser for ``<think>`` / ``</think>`` tags.

Some OpenAI-compatible models (MiniMax, certain local llama.cpp
configurations, …) embed their reasoning inside ``<think>…</think>``
blocks in the regular text stream rather than using a dedicated
``reasoning_content`` field.  :class:`_ThinkTagParser` extracts
those blocks and turns them into :class:`ThinkingDelta` events
while emitting the rest as :class:`TextDelta` events.

The parser is explicitly designed for **streaming**: tags may
arrive split across chunks (``<thi`` then ``nk>``), so anything
that could be the start of a partial tag is held back until enough
bytes arrive to decide.

Re-exported from :mod:`daemon.providers.openai_base` for backward
compatibility with existing imports.
"""

from __future__ import annotations

from daemon.engine.stream import TextDelta, ThinkingDelta

# Tags used by models (e.g. MiniMax) that embed thinking in text.
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


class _ThinkTagParser:
    """Streaming parser that separates ``<think>`` blocks from text.

    Models like MiniMax embed reasoning inside ``<think>...</think>``
    tags in the regular text stream.  This parser converts them into
    ``ThinkingDelta`` events while emitting the rest as ``TextDelta``.

    Handles tags split across multiple chunks (e.g. ``<thi`` + ``nk>``).
    """

    def __init__(self) -> None:
        self._inside_think = False
        self._buf = ""

    def feed(self, text: str) -> list[ThinkingDelta | TextDelta]:
        """Process a text chunk and return stream events.

        Args:
            text: Raw text chunk from the provider.

        Returns:
            List of ThinkingDelta / TextDelta events (may be empty
            if input is buffered waiting for a complete tag).
        """
        self._buf += text
        events: list[ThinkingDelta | TextDelta] = []

        while self._buf:
            if self._inside_think:
                close_pos = self._buf.find(_THINK_CLOSE)
                if close_pos == -1:
                    # Emit safe content, keep potential partial tag.
                    safe, remaining = _split_at_partial(self._buf, _THINK_CLOSE)
                    if safe:
                        events.append(ThinkingDelta(content=safe))
                    self._buf = remaining
                    break
                # Emit thinking up to the close tag.
                thinking = self._buf[:close_pos]
                if thinking:
                    events.append(ThinkingDelta(content=thinking))
                self._buf = self._buf[close_pos + len(_THINK_CLOSE) :]
                self._inside_think = False
            else:
                open_pos = self._buf.find(_THINK_OPEN)
                if open_pos == -1:
                    # Emit safe content, keep potential partial tag.
                    safe, remaining = _split_at_partial(self._buf, _THINK_OPEN)
                    if safe:
                        events.append(TextDelta(content=safe))
                    self._buf = remaining
                    break
                # Emit text before the open tag.
                before = self._buf[:open_pos]
                if before:
                    events.append(TextDelta(content=before))
                self._buf = self._buf[open_pos + len(_THINK_OPEN) :]
                self._inside_think = True

        return events


def _split_at_partial(buf: str, tag: str) -> tuple[str, str]:
    """Split buffer into safe-to-emit prefix and potential partial tag suffix.

    Scans backwards from the end of *buf* to find the longest suffix
    that is a prefix of *tag*.  Everything before that is safe to emit;
    the suffix must be kept buffered until more data arrives.

    Returns:
        ``(safe, remaining)`` — *safe* can be emitted immediately,
        *remaining* should stay in the buffer.
    """
    max_check = min(len(buf), len(tag) - 1)
    for length in range(max_check, 0, -1):
        if buf.endswith(tag[:length]):
            return buf[:-length], buf[-length:]
    # No partial match — everything is safe.
    return buf, ""


__all__ = ["_ThinkTagParser", "_split_at_partial"]
