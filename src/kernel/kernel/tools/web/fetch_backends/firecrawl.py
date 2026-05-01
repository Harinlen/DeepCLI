"""Firecrawl fetch backend — POST api.firecrawl.dev/v2/scrape.

Direct REST API call, no SDK. Verified by OpenClaw's TypeScript impl.
"""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult


class FirecrawlFetchBackend(FetchBackend):
    """Cloud/self-hosted Firecrawl scrape with JS rendering + anti-bot."""

    name = "firecrawl"

    def is_available(self) -> bool:
        return bool(
            os.getenv("FIRECRAWL_API_KEY", "").strip() or os.getenv("FIRECRAWL_API_URL", "").strip()
        )

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        base = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev").rstrip("/")
        api_key = os.getenv("FIRECRAWL_API_KEY", "")

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/v2/scrape",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "timeout": 60000,
                },
            )
            resp.raise_for_status()

        body = resp.json()
        data = body.get("data", {})
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        return FetchResult(
            url=metadata.get("sourceURL", url),
            content=(data.get("markdown") or "")[:max_chars],
            content_type="text/html",
            title=metadata.get("title", ""),
            status_code=metadata.get("statusCode", 200),
        )


__all__ = ["FirecrawlFetchBackend"]
