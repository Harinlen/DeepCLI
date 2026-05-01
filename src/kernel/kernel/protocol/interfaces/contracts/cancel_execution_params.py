"""Input to session/cancel_execution."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CancelExecutionParams(BaseModel):
    session_id: str
    kind: Literal["shell", "python", "any"] = "any"

