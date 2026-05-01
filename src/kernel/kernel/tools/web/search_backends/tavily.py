"""Tavily search backend — POST api.tavily.com/search."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


class TavilySearchBackend(SearchBackend):
    """Tavily structured search with domain filtering."""

    name = "tavily"

    def is_available(self) -> bool:
        return bool(os.getenv("TAVILY_API_KEY", "").strip())

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "query": query,
                    "max_results": min(limit, 20),
                    "api_key": os.getenv("TAVILY_API_KEY", ""),
                    "include_raw_content": False,
                    "include_images": False,
                },
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in results[:limit]
        ]


__all__ = ["TavilySearchBackend"]
