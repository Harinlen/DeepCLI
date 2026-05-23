"""Runtime configuration for WebFetch."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

WebFetchBackendName = Literal[
    "auto",
    "httpx",
    "crawl4ai",
    "firecrawl",
    "parallel",
    "exa",
    "tavily",
    "browser",
]


class WebFetchConfig(BaseModel):
    """User-managed WebFetch preferences.

    ``backend`` is a runtime preference, so it belongs in ConfigManager
    rather than FlagManager.  Backend-specific options are intentionally
    schemaless at this layer because external providers evolve faster
    than the kernel release cadence.
    """

    backend: WebFetchBackendName = "auto"
    backends: dict[str, dict[str, Any]] = Field(default_factory=dict)


__all__ = ["WebFetchBackendName", "WebFetchConfig"]
