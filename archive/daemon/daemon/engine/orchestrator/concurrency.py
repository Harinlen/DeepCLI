"""Tool-call concurrency planner.

Given a list of tool calls from a single LLM turn, partitions them
into ordered *execution groups*.  Calls within a group run in
parallel via :func:`asyncio.gather`; groups run sequentially.

The planner uses three inputs per call:

1. :class:`ConcurrencyHint` from the :class:`Tool` class attribute.
2. The concurrency key (for ``KEYED`` tools) — two ``KEYED`` calls
   with the same key are serialised.
3. Whether the call is *pre-approved* — i.e. the permission engine
   will return ``ALLOW`` without user interaction.  Calls that need
   a ``PermissionRequest`` prompt must run serially to avoid UX
   confusion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from daemon.extensions.tools.base import ConcurrencyHint
from daemon.providers.base import ToolUseContent


@dataclass(frozen=True, slots=True)
class ExecutionSlot:
    """One tool call annotated with concurrency metadata."""

    tc: ToolUseContent
    """The raw tool call from the LLM."""

    hint: ConcurrencyHint
    """Concurrency policy declared by the tool."""

    key: str | None = None
    """Conflict key for ``KEYED`` tools (e.g. file path)."""

    pre_approved: bool = False
    """``True`` when the permission engine will return ALLOW
    without prompting the user."""


@dataclass(slots=True)
class _GroupBuilder:
    """Accumulator for building a single parallel group."""

    slots: list[ExecutionSlot] = field(default_factory=list)
    keyed_keys: set[str] = field(default_factory=set)

    def can_accept(self, slot: ExecutionSlot) -> bool:
        """Return whether *slot* can join this group."""
        if not slot.pre_approved:
            return False
        if slot.hint is ConcurrencyHint.SERIAL:
            return False
        if slot.hint is ConcurrencyHint.KEYED:
            return slot.key is not None and slot.key not in self.keyed_keys
        # PARALLEL — always welcome
        return True

    def add(self, slot: ExecutionSlot) -> None:
        """Append *slot* to the group."""
        self.slots.append(slot)
        if slot.hint is ConcurrencyHint.KEYED and slot.key is not None:
            self.keyed_keys.add(slot.key)

    @property
    def empty(self) -> bool:
        return len(self.slots) == 0


def plan_execution_groups(
    slots: list[ExecutionSlot],
) -> list[list[ExecutionSlot]]:
    """Partition tool calls into ordered execution groups.

    Within each group calls may run in parallel.  Groups themselves
    run sequentially.

    Algorithm (left-to-right scan):

    1. Maintain a *current group* (``_GroupBuilder``).
    2. For each slot:

       - ``PARALLEL`` + pre-approved → join current group.
       - ``KEYED`` + pre-approved + key not yet taken → join current
         group, record the key.
       - ``KEYED`` + pre-approved + key **conflict** → flush the
         current group, start a new one containing this slot.
       - ``SERIAL`` or ``!pre_approved`` → flush the current group,
         emit the slot as a singleton group.

    3. Flush any remaining group at the end.

    Returns:
        Ordered list of groups.  Each group is a non-empty list of
        :class:`ExecutionSlot`.
    """
    if not slots:
        return []

    groups: list[list[ExecutionSlot]] = []
    current = _GroupBuilder()

    for slot in slots:
        if current.can_accept(slot):
            current.add(slot)
            continue

        # Cannot join — flush current group first (if non-empty).
        if not current.empty:
            groups.append(current.slots)
            current = _GroupBuilder()

        # SERIAL or not pre-approved → always a singleton group.
        if slot.hint is ConcurrencyHint.SERIAL or not slot.pre_approved:
            groups.append([slot])
        else:
            # KEYED with key conflict — start a new group with this slot.
            current.add(slot)

    # Flush trailing group.
    if not current.empty:
        groups.append(current.slots)

    return groups


__all__ = [
    "ConcurrencyHint",
    "ExecutionSlot",
    "plan_execution_groups",
]
