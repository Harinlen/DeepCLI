"""Output from user REPL execution requests."""

from __future__ import annotations

from pydantic import BaseModel


class ExecutionResult(BaseModel):
    exit_code: int
    cancelled: bool = False

