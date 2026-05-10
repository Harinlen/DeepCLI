"""Small JSON-shaped IPC helpers for the REPL worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplRunResult:
    stdout: str
    stderr: str
    value: Any = None
    error: str | None = None
    reset: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


__all__ = ["ReplRunResult"]
