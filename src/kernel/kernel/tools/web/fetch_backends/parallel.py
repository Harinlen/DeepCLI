"""Parallel fetch backend — POST api.parallel.ai/extract."""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult


class ParallelFetchBackend(FetchBackend):
    """Parallel.ai full-content extraction."""

    name = "parallel"

    def is_available(self) -> bool:
        return bool(os.getenv("PARALLEL_API_KEY", "").strip())

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.parallel.ai/extract",
                headers={
                    "Authorization": f"Bearer {os.getenv('PARALLEL_API_KEY', '')}",
                    "Content-Type": "application/json",
                },
                json={"urls": [url], "full_content": True},
            )
            resp.raise_for_status()

        results = resp.json().get("results", [])
        if not results:
            return FetchResult(
                url=url,
                content="",
                content_type="",
                error="no results from Parallel",
            )
        r = results[0]
        return FetchResult(
            url=r.get("url", url),
            content=(r.get("full_content") or "")[:max_chars],
            content_type="text/html",
            title=r.get("title", ""),
        )


__all__ = ["ParallelFetchBackend"]
