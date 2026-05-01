"""Task ID generation.

Design reference: ``docs/plans/task-manager.md`` § 1.2.
Claude Code equivalent: ``src/Task.ts:98-106``.

Format: single-char type prefix + 8-char random suffix from ``[0-9a-z]``.
"""

from __future__ import annotations

import secrets

from kernel.tasks.types import TaskType

_PREFIXES: dict[TaskType, str] = {
    TaskType.local_bash: "b",
    TaskType.local_agent: "a",
    TaskType.monitor: "m",
}

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_SUFFIX_LENGTH = 8


def generate_task_id(task_type: TaskType) -> str:
    """Generate a unique task ID with a type-based prefix."""
    prefix = _PREFIXES.get(task_type, "x")
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(_SUFFIX_LENGTH))
    return prefix + suffix


__all__ = ["generate_task_id"]
