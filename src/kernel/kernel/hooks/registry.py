"""HookRegistry — in-memory map from event to ordered handler list.

Stateless beyond the dict itself; stores nothing per session.  The
HookManager owns one instance for the lifetime of the kernel and
populates it during ``startup``.

Order matters: ``register`` appends, and ``HookManager.fire`` walks
the list in registration order.  The first handler that raises
``HookBlock`` short-circuits remaining handlers (when the event
allows blocking).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from kernel.hooks.types import HookEvent, HookHandler


class HookRegistry:
    """Append-only ``HookEvent → [handler, ...]`` table."""

    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[HookHandler]] = defaultdict(list)

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        """Append a handler to the list for ``event``.

        The same handler may be registered for multiple events by
        calling this once per event — that is how a single
        ``handler.py`` subscribes to ``events: [a, b]`` in HOOK.md.
        """
        self._handlers[event].append(handler)

    def get(self, event: HookEvent) -> list[HookHandler]:
        """Return the list of handlers for ``event`` in registration order.

        Returns a fresh list copy to insulate callers from later
        ``register`` calls during iteration.  Empty events return ``[]``.
        """
        return list(self._handlers.get(event, ()))

    def __len__(self) -> int:
        """Total number of (event, handler) registrations across all events."""
        return sum(len(hs) for hs in self._handlers.values())

    def events(self) -> Iterator[HookEvent]:
        """Iterate the events that have at least one registered handler."""
        return iter(self._handlers.keys())

    def clear(self) -> None:
        """Drop all registrations.  Used by tests; the kernel never
        clears the registry while running."""
        self._handlers.clear()


__all__ = ["HookRegistry"]
