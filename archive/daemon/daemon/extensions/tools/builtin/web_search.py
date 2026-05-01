"""WebSearch tool — pluggable search backend.

Backend selection lives in ``daemon.extensions.tools.web_backends``.
Default behaviour: use DuckDuckGo HTML scraping unless a specific
backend is explicitly requested in config.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.web_backends import SearchBackend, select_backend

logger = logging.getLogger(__name__)


class WebSearchTool(Tool):
    """Search the web and return title + URL + snippet for the top hits."""

    name = "web_search"
    description = (
        "Search the web for information on a topic. Returns a list of "
        "hits with title, URL, and snippet. Pass ``query`` (the search "
        "string) and optionally ``limit`` (number of hits, default 10)."
    )
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.PARALLEL

    class Input(BaseModel):
        query: str = Field(description="Search query.")
        limit: int = Field(default=10, ge=1, le=25, description="Number of hits to return.")

    def __init__(self, backend: SearchBackend | None = None, preferred: str | None = None) -> None:
        """Construct with an optional pre-built backend.

        Args:
            backend: Injected backend (used by tests).  When ``None``
                the tool picks one on the fly from config + env.
            preferred: Config-level preference (``"brave"``, etc).
        """
        self._preferred = preferred
        self._injected = backend

    def _resolve_backend(self) -> SearchBackend | None:
        if self._injected is not None:
            return self._injected
        return select_backend(
            self._preferred,
            os.environ.get("BRAVE_API_KEY"),
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            google_cse_id=os.environ.get("GOOGLE_CSE_ID"),
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        validated = self.Input.model_validate(params)
        backend = self._resolve_backend()
        if backend is None:
            return ToolResult(
                output=(
                    "No web_search backend available. Configure one of: "
                    "BRAVE_API_KEY or "
                    "GOOGLE_API_KEY + GOOGLE_CSE_ID; or set "
                    "tools.web_search.backend in config.yaml."
                ),
                is_error=True,
            )

        try:
            results = await backend.search(validated.query, limit=validated.limit)
        except Exception as exc:  # noqa: BLE001
            logger.exception("web_search backend error")
            return ToolResult(output=f"Search failed: {exc}", is_error=True)

        if not results:
            return ToolResult(output=f"No results for {validated.query!r}.")

        lines: list[str] = [
            "Note: Dates in snippets below are from the original pages, "
            "not the current date. Refer to the system Environment for today's date.",
            "",
        ]
        for i, hit in enumerate(results, start=1):
            lines.append(f"{i}. {hit.title}\n   {hit.url}\n   {hit.snippet}")
        return ToolResult(output="\n\n".join(lines))
