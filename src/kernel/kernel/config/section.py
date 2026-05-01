"""Section state plus owner/reader wrappers for ConfigManager.

Design invariants (see ``docs/design.md`` § ConfigManager):

- **Single source of truth** — every ``(file, section)`` is backed by
  exactly one :class:`_Section` instance.  Owner and readers receive
  two different thin wrappers over the same state, so writes via the
  owner are visible to readers on the very next ``get()``.

- **First bind wins** — enforcement lives in :class:`ConfigManager`,
  not here.  This module only distinguishes "can mutate" from "can
  only read" via two wrapper types.

- **Write-then-commit** — :meth:`_Section.update` validates, writes
  the YAML file atomically, and only then swaps the in-memory value.
  The ``changed`` signal is emitted outside the lock so a slot may
  recursively trigger other section updates without deadlocking.

- **Read-only means read-only at the type level** — ``ReadOnlySection``
  literally has no ``update`` method, so IDE / mypy stops misuse
  before it reaches runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Generic, TypeVar

import yaml
from pydantic import BaseModel

from kernel.signal import Signal

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class _Section(Generic[T]):
    """Shared mutable state for one ``(file, section)`` pair.

    ``ConfigManager`` constructs one of these per bound section and
    hands :class:`MutableSection` / :class:`ReadOnlySection` wrappers
    out to subsystems.  The class is intentionally **private**:
    nothing outside :mod:`kernel.config` should import it directly.
    """

    def __init__(
        self,
        *,
        file: str,
        section: str,
        schema: type[T],
        current: T,
        write_path: Path,
    ) -> None:
        self.file = file
        self.section = section
        self.schema = schema
        self._current: T = current
        self._write_path: Path = write_path
        self._lock = asyncio.Lock()
        # Signal payload is ``(old, new)`` — matches the design doc.
        self.changed: Signal[[T, T]] = Signal()

    def get(self) -> T:
        return self._current

    async def update(self, new_value: T) -> None:
        """Validate, persist, swap, notify.

        Validation runs outside the lock (it's pure, no shared state).
        The write happens inside the lock so two concurrent updates
        cannot interleave disk writes.  The signal is emitted *after*
        releasing the lock, so slots are free to call back into
        ``update`` on any other section without deadlocking.

        Failure semantics:

        - validation or disk write fails → in-memory value is
          untouched, signal is not emitted, exception propagates to
          the caller.
        - an individual slot raises → :class:`Signal` logs and
          continues; ``update`` itself returns success because disk
          and memory have already moved.
        """
        # (1) Early, pure validation — catches bad inputs before we
        # grab the lock or touch the filesystem.
        validated = self.schema.model_validate(new_value.model_dump())

        async with self._lock:
            old = self._current
            # (2) Write-ahead: persist before mutating memory so that
            # a crashed write leaves disk and memory consistent.
            await asyncio.to_thread(self._write_atomic, validated)
            # (3) Commit in-memory state only after the write landed.
            self._current = validated

        # (4) Fire change notification outside the lock.
        await self.changed.emit(old, validated)

    def _write_atomic(self, value: T) -> None:
        """Rewrite the owning YAML file with this section patched in.

        We read-modify-write the *entire* file so sibling sections are
        preserved byte-for-byte semantically (their dump still matches
        the schema defaults).  Fields equal to the schema default are
        stripped via ``exclude_defaults=True`` so the file stays tidy
        as defaults change in code.

        Atomicity comes from writing to a sibling tmp file and calling
        :func:`os.replace`, which is atomic on POSIX and Windows.
        """
        existing: dict[str, Any] = {}
        if self._write_path.exists():
            with self._write_path.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                existing = loaded
            else:
                logger.warning(
                    "Ignoring non-mapping content in %s during section "
                    "update — file will be rewritten",
                    self._write_path,
                )

        dumped = value.model_dump(exclude_defaults=True)
        if dumped:
            existing[self.section] = dumped
        else:
            # All fields at default → drop the section entirely so the
            # file doesn't accumulate empty stanzas.
            existing.pop(self.section, None)

        self._write_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._write_path.with_suffix(self._write_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(existing, fh, sort_keys=True, allow_unicode=True)
        os.replace(tmp_path, self._write_path)


class MutableSection(Generic[T]):
    """Owner-facing wrapper: read, write, subscribe.

    Exactly one :class:`MutableSection` exists per ``(file, section)``
    — :class:`ConfigManager` enforces this via its first-bind-wins
    check.  The owner is the only caller allowed to invoke
    :meth:`update`; everyone else holds a :class:`ReadOnlySection`.
    """

    def __init__(self, section: _Section[T]) -> None:
        self._section = section

    def get(self) -> T:
        return self._section.get()

    async def update(self, new_value: T) -> None:
        await self._section.update(new_value)

    @property
    def changed(self) -> Signal[[T, T]]:
        return self._section.changed


class ReadOnlySection(Generic[T]):
    """Reader-facing wrapper — no ``update`` method, on purpose.

    Any number of :class:`ReadOnlySection` instances can coexist for
    the same underlying :class:`_Section`.  They are cheap proxies
    that delegate :meth:`get` and :attr:`changed` to the shared
    state, so writes made by the owner are immediately visible.
    """

    def __init__(self, section: _Section[T]) -> None:
        self._section = section

    def get(self) -> T:
        return self._section.get()

    @property
    def changed(self) -> Signal[[T, T]]:
        return self._section.changed
