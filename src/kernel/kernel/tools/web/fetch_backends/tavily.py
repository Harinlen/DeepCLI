"""Tavily fetch backend — POST api.tavily.com/extract."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult


class TavilyFetchBackend(FetchBackend):
    """Tavily content extraction API."""

    name = "tavily"

    def is_available(self) -> bool:
        return bool(os.getenv("TAVILY_API_KEY", "").strip())

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.tavily.com/extract",
                json={
                    "urls": [url],
                    "api_key": os.getenv("TAVILY_API_KEY", ""),
                    "include_images": False,
                },
            )
            resp.raise_for_status()

        results = resp.json().get("results", [])
        if not results:
            return FetchResult(
                url=url,
                content="",
                content_type="",
                error="no results from Tavily",
            )
        r = results[0]
        content = r.get("raw_content") or r.get("content") or ""
        return FetchResult(
            url=r.get("url", url),
            content=content[:max_chars],
            content_type="text/html",
            title=r.get("title", ""),
        )


__all__ = ["TavilyFetchBackend"]
