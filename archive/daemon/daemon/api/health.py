"""Health check endpoint."""

from __future__ import annotations

import time

from fastapi import APIRouter

router = APIRouter()

# Captured at module import — updates when uvicorn reloads or daemon restarts.
_STARTED_AT = time.time()


@router.get("/health")
async def health() -> dict[str, str | float]:
    """Return daemon health status.

    Used by CLI and monitoring to verify the daemon is running.
    Includes ``started_at`` (Unix epoch) so clients can detect
    restarts / reload events.
    """
    return {"status": "ok", "started_at": _STARTED_AT}
