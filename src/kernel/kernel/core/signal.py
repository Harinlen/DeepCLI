"""Minimal async Signal/Slot primitive.

A tiny implementation that ConfigManager uses to notify subscribers
about section updates.  We roll our own rather than pull in blinker /
PyQt because the semantics we need are narrow and stable:

- async-only slots (no sync callbacks — avoids mixed-model headaches)
- serial ``await`` during ``emit`` (predictable ordering)
- per-slot exception isolation (one bad slot does not poison others)
- explicit ``disconnect`` returned from ``connect`` — no weakref magic,
  subscribers are responsible for their own cleanup

The module is standalone on purpose: no kernel imports, so any
subsystem can depend on it without risking a cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Generic, ParamSpec

logger = logging.getLogger(__name__)

P = ParamSpec("P")


class Signal(Generic[P]):
    """A typed async signal.

    Parameters
    ----------
    P:
        The argument signature of the slot.  Use ``Signal[[int, str]]``
        or ``Signal[[OldT, NewT]]`` to describe the emitted payload.

    Notes
    -----
    Slots are stored in registration order and awaited serially during
    :meth:`emit`.  Exceptions raised inside a slot are logged and
    swallowed so one misbehaving subscriber cannot abort the broadcast
    or surface back to the caller of ``emit``.
    """

    def __init__(self) -> None:
        self._slots: list[Callable[P, Awaitable[None]]] = []

    def connect(self, slot: Callable[P, Awaitable[None]]) -> Callable[[], None]:
        """Register ``slot`` and return an idempotent disconnect callable.

        The same ``slot`` can be connected multiple times; each
        ``connect`` call must be paired with its own ``disconnect``.
        Calling the returned disconnect more than once is a no-op.
        """
        self._slots.append(slot)

        def disconnect() -> None:
            try:
                self._slots.remove(slot)
            except ValueError:
                pass  # already disconnected — allow idempotent cleanup

        return disconnect

    async def emit(self, *args: P.args, **kwargs: P.kwargs) -> None:
        """Invoke every connected slot in registration order.

        Iterates over a snapshot of the slot list so that slots which
        ``disconnect`` themselves (or other slots) during dispatch do
        not mutate the iterator.  Per-slot exceptions are logged via
        ``logger.exception`` and do not interrupt the broadcast.
        """
        for slot in list(self._slots):
            try:
                await slot(*args, **kwargs)
            except Exception:
                logger.exception("signal slot %r failed", slot)
