"""Google Custom Search backend — GET googleapis.com/customsearch/v1."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


class GoogleSearchBackend(SearchBackend):
    """Google Custom Search JSON API."""

    name = "google"

    def is_available(self) -> bool:
        return bool(
            os.getenv("GOOGLE_API_KEY", "").strip() and os.getenv("GOOGLE_CSE_ID", "").strip()
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "q": query,
                    "key": os.getenv("GOOGLE_API_KEY", ""),
                    "cx": os.getenv("GOOGLE_CSE_ID", ""),
                    "num": min(limit, 10),
                },
            )
            resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("link", ""),
                snippet=r.get("snippet", ""),
            )
            for r in items[:limit]
        ]


__all__ = ["GoogleSearchBackend"]
