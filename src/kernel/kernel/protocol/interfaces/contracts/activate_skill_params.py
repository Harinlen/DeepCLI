"""Parameters for deterministic user-invoked skill activation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ActivateSkillParams(BaseModel):
    """Activate a user-invocable skill and run one prompt turn."""

    session_id: str
    skill: str
    args: str = ""
    meta: dict[str, Any] | None = None
