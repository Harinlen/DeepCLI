"""Cleanup registry — centralised shutdown management.

Any module can register an async cleanup function at any time.
During daemon shutdown, ``run_cleanups()`` executes all registered
callbacks concurrently.  This decouples individual subsystems from
the top-level lifespan code: adding a new subsystem never requires
editing ``app.py``.

Pattern borrowed from Claude Code's ``cleanupRegistry.ts``.

Usage::

    from daemon.lifecycle import register_cleanup

    unreg = register_cleanup(client.close)   # register
    unreg()                                  # optional: unregister early
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Ordered list preserves registration order for deterministic shutdown.
_cleanups: list[Callable[[], Awaitable[None]]] = []


def register_cleanup(fn: Callable[[], Awaitable[None]]) -> Callable[[], None]:
    """Register an async function to run during shutdown.

    Args:
        fn: Async callable that performs cleanup (e.g. close a
            connection, cancel a task).

    Returns:
        A no-arg callable that removes the registration.  Useful when
        the resource is released early (before daemon shutdown).
    """
    _cleanups.append(fn)

    def _unregister() -> None:
        try:
            _cleanups.remove(fn)
        except ValueError:
            pass  # Already removed

    return _unregister


async def run_cleanups() -> None:
    """Execute all registered cleanup functions concurrently.

    Errors in individual callbacks are logged but do not prevent
    other callbacks from running.  The registry is cleared after
    execution.
    """
    if not _cleanups:
        return

    logger.debug("Running %d cleanup callbacks", len(_cleanups))

    results = await asyncio.gather(
        *(fn() for fn in _cleanups),
        return_exceptions=True,
    )

    for fn, result in zip(_cleanups, results):
        if isinstance(result, Exception):
            logger.warning(
                "Cleanup callback %s failed: %s",
                getattr(fn, "__qualname__", repr(fn)),
                result,
            )

    _cleanups.clear()


def reset_for_testing() -> None:
    """Clear the registry — for use in tests only."""
    _cleanups.clear()
