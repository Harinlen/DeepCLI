"""Tests for :mod:`kernel.signal`.

The Signal primitive is tiny, so the tests focus on the invariants
that ConfigManager (and future subscribers) actually rely on:

- slots fire in registration order
- disconnect stops further delivery and is idempotent
- exceptions inside one slot do not break other slots
- mutating the slot list during ``emit`` is safe
"""

from __future__ import annotations

from kernel.signal import Signal


async def test_emit_calls_slots_with_args() -> None:
    signal: Signal[[int, str]] = Signal()
    seen: list[tuple[int, str]] = []

    async def slot(n: int, s: str) -> None:
        seen.append((n, s))

    signal.connect(slot)
    await signal.emit(1, "hello")

    assert seen == [(1, "hello")]


async def test_multiple_slots_fire_in_registration_order() -> None:
    signal: Signal[[int]] = Signal()
    order: list[str] = []

    async def first(_: int) -> None:
        order.append("first")

    async def second(_: int) -> None:
        order.append("second")

    signal.connect(first)
    signal.connect(second)
    await signal.emit(0)

    assert order == ["first", "second"]


async def test_disconnect_stops_delivery() -> None:
    signal: Signal[[int]] = Signal()
    seen: list[int] = []

    async def slot(value: int) -> None:
        seen.append(value)

    disconnect = signal.connect(slot)
    await signal.emit(1)
    disconnect()
    await signal.emit(2)

    assert seen == [1]


async def test_disconnect_is_idempotent() -> None:
    signal: Signal[[]] = Signal()

    async def slot() -> None:
        pass

    disconnect = signal.connect(slot)
    disconnect()
    # A second call should not raise even though the slot is gone.
    disconnect()


async def test_slot_exception_does_not_stop_other_slots() -> None:
    signal: Signal[[]] = Signal()
    after_bad: list[bool] = []

    async def bad() -> None:
        raise RuntimeError("boom")

    async def good() -> None:
        after_bad.append(True)

    signal.connect(bad)
    signal.connect(good)
    await signal.emit()  # must not raise

    assert after_bad == [True]


async def test_slot_may_disconnect_during_emit() -> None:
    """A slot that disconnects itself mid-emit should not break
    iteration for other slots — ``emit`` iterates a snapshot."""
    signal: Signal[[]] = Signal()
    calls: list[str] = []
    disconnects: dict[str, object] = {}

    async def self_removing() -> None:
        calls.append("self_removing")
        disconnects["self_removing"]()  # type: ignore[operator]

    async def tail() -> None:
        calls.append("tail")

    disconnects["self_removing"] = signal.connect(self_removing)
    signal.connect(tail)

    await signal.emit()
    assert calls == ["self_removing", "tail"]

    # Second emit: only ``tail`` remains.
    calls.clear()
    await signal.emit()
    assert calls == ["tail"]


async def test_same_slot_connected_twice_fires_twice() -> None:
    signal: Signal[[]] = Signal()
    hits: list[int] = []

    async def slot() -> None:
        hits.append(1)

    signal.connect(slot)
    signal.connect(slot)
    await signal.emit()

    assert hits == [1, 1]
