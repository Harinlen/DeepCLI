"""Shared uvicorn runtime settings for kernel entrypoints."""

from __future__ import annotations

import sys


def uvicorn_loop() -> str:
    """Return the uvicorn event loop policy for this platform."""

    if sys.platform == "win32":
        return "asyncio"
    return "uvloop"


__all__ = ["uvicorn_loop"]
