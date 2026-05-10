"""GET / — health check and kernel metadata."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

import kernel

router = APIRouter()

_boot_time: float = time.time()


@router.get("/")
async def health(request: Request) -> dict[str, object]:
    """Return kernel version, boot time (UTC unix timestamp), and runtime status."""
    return {
        "name": "mustang-kernel",
        "version": kernel.__version__,
        "boot_time": _boot_time,
    }
