"""Batcher — generic streaming-chunk coalescer.

LLMs emit text in rapid small fragments (10-20 ms, 5-20 chars each).
Forwarding each fragment as its own WebSocket frame wastes bandwidth
and CPU.  A ``Batcher`` coalesces adjacent mergeable fragments into a
single frame sent at most once per ``window_ms`` milliseconds.

Design
------
``Batcher`` is a protocol-agnostic base.  Concrete subclasses or
instances supply a ``is_mergeable`` predicate and a ``merge`` function
so the coalescing logic stays generic while the definition of
"mergeable" remains protocol-specific.

For ACP: only the three ``*_chunk`` session-update variants with
``type == "text"`` content are mergeable.  Everything else (tool_call,
plan, mode updates, …) flushes the buffer immediately before being
sent on its own.

Usage pattern (within a prompt-turn loop)
------------------------------------------
::

    async with Batcher(sender, is_mergeable, merge, window_ms=50) as b:
        async for event in orchestrator.run(...):
            outbound_msg = event_mapper.map(event)
            await b.feed(outbound_msg)
    # context-manager exit flushes any remaining buffer
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

M = TypeVar("M")
"""The outbound message type (e.g. a Pydantic model)."""


class Batcher(Generic[M]):
    """Async context-manager that coalesces mergeable outbound messages.

    Parameters
    ----------
    send:
        Coroutine that delivers a single message to the client.
    is_mergeable:
        Returns ``True`` if ``msg`` can be held in the buffer for
        coalescing.
    merge:
        Given two consecutive mergeable messages, return their
        coalesced form.  Must be associative.
    window_ms:
        Maximum milliseconds to hold a buffer before flushing.
        Defaults to 50 ms (≈ 20 fps — smooth for humans, 5× less
        frames than uncoalesced 200 Hz output).
    """

    def __init__(
        self,
        send: Callable[[M], Any],
        is_mergeable: Callable[[M], bool],
        merge: Callable[[M, M], M],
        window_ms: float = 50.0,
    ) -> None:
        self._send = send
        self._is_mergeable = is_mergeable
        self._merge = merge
        self._window_s = window_ms / 1000.0
        self._buffer: M | None = None
        self._timer_task: asyncio.Task | None = None

    async def __aenter__(self) -> Batcher[M]:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.flush()
        if self._timer_task is not None and not self._timer_task.done():
            self._timer_task.cancel()

    async def feed(self, msg: M) -> None:
        """Accept an outbound message.

        Mergeable messages are held in the buffer until the window
        expires.  Non-mergeable messages flush the buffer first, then
        are sent immediately on their own.
        """
        if self._is_mergeable(msg):
            if self._buffer is None:
                self._buffer = msg
                self._arm_timer()
            else:
                self._buffer = self._merge(self._buffer, msg)
        else:
            await self.flush()
            await self._send(msg)

    async def flush(self) -> None:
        """Send the current buffer immediately (if non-empty)."""
        if self._timer_task is not None and not self._timer_task.done():
            self._timer_task.cancel()
            self._timer_task = None
        if self._buffer is not None:
            msg = self._buffer
            self._buffer = None
            await self._send(msg)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _arm_timer(self) -> None:
        """Schedule a flush after ``window_ms`` milliseconds."""
        if self._timer_task is not None and not self._timer_task.done():
            return
        self._timer_task = asyncio.create_task(self._timer_flush())

    async def _timer_flush(self) -> None:
        await asyncio.sleep(self._window_s)
        await self.flush()
