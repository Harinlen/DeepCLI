"""Exa search backend — POST api.exa.ai/search."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


class ExaSearchBackend(SearchBackend):
    """Exa semantic search with highlights."""

    name = "exa"

    def is_available(self) -> bool:
        return bool(os.getenv("EXA_API_KEY", "").strip())

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": os.getenv("EXA_API_KEY", ""),
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "numResults": limit,
                    "contents": {"highlights": True},
                },
            )
            resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=" ".join(r.get("highlights", [])),
            )
            for r in results[:limit]
        ]


__all__ = ["ExaSearchBackend"]
