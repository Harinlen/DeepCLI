"""Parameters for retrieving current session usage."""

from __future__ import annotations

from pydantic import BaseModel


class GetUsageParams(BaseModel):
    """Input to ``_mustang.agent/session/get_usage``."""

    session_id: str | None = None
