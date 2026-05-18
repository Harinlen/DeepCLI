"""In-memory idempotency table for Access Router turns."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IdempotencyEntry:
    """Stored idempotent operation result."""

    key: str
    status: str
    result: dict[str, object]


class IdempotencyStore:
    """Small idempotency store used before durable router DB lands."""

    def __init__(self) -> None:
        self._entries: dict[str, IdempotencyEntry] = {}

    def get(self, key: str) -> IdempotencyEntry | None:
        return self._entries.get(key)

    def put(self, key: str, *, status: str, result: dict[str, object]) -> IdempotencyEntry:
        entry = IdempotencyEntry(key=key, status=status, result=result)
        self._entries[key] = entry
        return entry
