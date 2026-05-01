"""Brave search backend — GET api.search.brave.com/res/v1/web/search."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


class BraveSearchBackend(SearchBackend):
    """Brave Search API — structured results with language/region filters."""

    name = "brave"

    def is_available(self) -> bool:
        return bool(os.getenv("BRAVE_API_KEY", "").strip())

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(limit, 20)},
                headers={
                    "X-Subscription-Token": os.getenv("BRAVE_API_KEY", ""),
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in data.get("web", {}).get("results", [])[:limit]
        ]


__all__ = ["BraveSearchBackend"]
