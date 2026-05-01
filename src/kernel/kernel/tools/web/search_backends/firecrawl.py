"""Firecrawl search backend — POST api.firecrawl.dev/v2/search."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


class FirecrawlSearchBackend(SearchBackend):
    """Firecrawl search API — direct REST, no SDK."""

    name = "firecrawl"

    def is_available(self) -> bool:
        return bool(
            os.getenv("FIRECRAWL_API_KEY", "").strip() or os.getenv("FIRECRAWL_API_URL", "").strip()
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        base = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev").rstrip("/")
        api_key = os.getenv("FIRECRAWL_API_KEY", "")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base}/v2/search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "limit": limit},
            )
            resp.raise_for_status()
        data = resp.json().get("data", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in data[:limit]
        ]


__all__ = ["FirecrawlSearchBackend"]
