"""Search backend registry and fallback chain."""

from __future__ import annotations

import logging
import os

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult

logger = logging.getLogger(__name__)


def _has_env(name: str) -> bool:
    val = os.getenv(name)
    return bool(val and val.strip())


def get_available_backends() -> list[SearchBackend]:
    """Return currently-available backend instances in priority order."""
    from kernel.tools.web.search_backends.brave import BraveSearchBackend
    from kernel.tools.web.search_backends.duckduckgo import DuckDuckGoSearchBackend
    from kernel.tools.web.search_backends.exa import ExaSearchBackend
    from kernel.tools.web.search_backends.firecrawl import FirecrawlSearchBackend
    from kernel.tools.web.search_backends.google import GoogleSearchBackend
    from kernel.tools.web.search_backends.kimi import KimiSearchBackend
    from kernel.tools.web.search_backends.parallel import ParallelSearchBackend
    from kernel.tools.web.search_backends.perplexity import PerplexitySearchBackend
    from kernel.tools.web.search_backends.tavily import TavilySearchBackend
    from kernel.tools.web.search_backends.xai import XaiSearchBackend

    priority: list[type[SearchBackend]] = [
        BraveSearchBackend,
        GoogleSearchBackend,
        ExaSearchBackend,
        TavilySearchBackend,
        FirecrawlSearchBackend,
        ParallelSearchBackend,
        PerplexitySearchBackend,
        KimiSearchBackend,
        XaiSearchBackend,
        DuckDuckGoSearchBackend,  # always available
    ]
    return [cls() for cls in priority if cls().is_available()]


async def search_with_fallback(
    query: str,
    limit: int,
    *,
    preferred: str | None = None,
    backends: list[SearchBackend] | None = None,
) -> tuple[list[SearchResult], str]:
    """Try each backend in order; return (results, backend_name)."""
    if backends is None:
        backends = get_available_backends()

    if preferred:
        backends = sorted(backends, key=lambda b: 0 if b.name == preferred else 1)

    errors: list[str] = []
    for backend in backends:
        try:
            results = await backend.search(query, limit=limit)
            if results:
                return results, backend.name
            errors.append(f"{backend.name}: 0 results")
        except Exception as exc:
            errors.append(f"{backend.name}: {exc}")
            continue

    return [], f"all backends failed: {'; '.join(errors)}"


__all__ = [
    "SearchBackend",
    "SearchResult",
    "get_available_backends",
    "search_with_fallback",
]
