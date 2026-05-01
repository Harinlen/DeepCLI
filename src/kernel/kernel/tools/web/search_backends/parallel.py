"""Parallel search backend — POST api.parallel.ai/search."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


class ParallelSearchBackend(SearchBackend):
    """Parallel.ai agentic search."""

    name = "parallel"

    def is_available(self) -> bool:
        return bool(os.getenv("PARALLEL_API_KEY", "").strip())

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        mode = os.getenv("PARALLEL_SEARCH_MODE", "agentic")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.parallel.ai/search",
                headers={
                    "Authorization": f"Bearer {os.getenv('PARALLEL_API_KEY', '')}",
                    "Content-Type": "application/json",
                },
                json={
                    "search_queries": [query],
                    "objective": query,
                    "mode": mode,
                    "max_results": min(limit, 20),
                },
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=" ".join(r.get("excerpts", [])) if r.get("excerpts") else "",
            )
            for r in results[:limit]
        ]


__all__ = ["ParallelSearchBackend"]
