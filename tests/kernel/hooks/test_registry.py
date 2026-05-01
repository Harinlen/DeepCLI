"""HookRegistry — append + get + multi-event registration semantics."""

from __future__ import annotations

from kernel.hooks.registry import HookRegistry
from kernel.hooks.types import HookEvent


def test_get_empty_returns_empty_list() -> None:
    reg = HookRegistry()
    assert reg.get(HookEvent.PRE_TOOL_USE) == []


def test_register_appends_in_order() -> None:
    reg = HookRegistry()

    async def h1(ctx):  # type: ignore[no-untyped-def]
        return None

    async def h2(ctx):  # type: ignore[no-untyped-def]
        return None

    reg.register(HookEvent.STOP, h1)
    reg.register(HookEvent.STOP, h2)

    assert reg.get(HookEvent.STOP) == [h1, h2]


def test_get_returns_independent_copy() -> None:
    """Mutating the returned list must not affect future ``get`` calls."""
    reg = HookRegistry()

    async def h(ctx):  # type: ignore[no-untyped-def]
        return None

    reg.register(HookEvent.STOP, h)
    snapshot = reg.get(HookEvent.STOP)
    snapshot.clear()
    assert reg.get(HookEvent.STOP) == [h]


def test_same_handler_can_register_for_multiple_events() -> None:
    """The HOOK.md ``events: [a, b]`` flow registers the same handler twice."""
    reg = HookRegistry()

    async def h(ctx):  # type: ignore[no-untyped-def]
        return None

    reg.register(HookEvent.PRE_TOOL_USE, h)
    reg.register(HookEvent.POST_TOOL_USE, h)

    assert reg.get(HookEvent.PRE_TOOL_USE) == [h]
    assert reg.get(HookEvent.POST_TOOL_USE) == [h]


def test_len_counts_all_registrations() -> None:
    reg = HookRegistry()

    async def h(ctx):  # type: ignore[no-untyped-def]
        return None

    reg.register(HookEvent.PRE_TOOL_USE, h)
    reg.register(HookEvent.POST_TOOL_USE, h)
    reg.register(HookEvent.POST_TOOL_USE, h)

    assert len(reg) == 3


def test_clear_drops_all_registrations() -> None:
    reg = HookRegistry()

    async def h(ctx):  # type: ignore[no-untyped-def]
        return None

    reg.register(HookEvent.STOP, h)
    reg.clear()
    assert reg.get(HookEvent.STOP) == []
    assert len(reg) == 0


def test_events_iter_returns_only_populated_events() -> None:
    reg = HookRegistry()

    async def h(ctx):  # type: ignore[no-untyped-def]
        return None

    reg.register(HookEvent.STOP, h)
    assert set(reg.events()) == {HookEvent.STOP}
