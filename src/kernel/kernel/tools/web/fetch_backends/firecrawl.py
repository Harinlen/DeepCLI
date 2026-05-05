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
        timeout_seconds = _env_int("FIRECRAWL_TIMEOUT_SECONDS", 60)
        max_age_ms = _env_int("FIRECRAWL_MAX_AGE_MS", 172_800_000)
        proxy = os.getenv("FIRECRAWL_PROXY", "auto").strip() or "auto"
        store_in_cache = os.getenv("FIRECRAWL_STORE_IN_CACHE", "true").strip().lower() != "false"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                _resolve_endpoint(base),
                headers=headers,
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "timeout": timeout_seconds * 1000,
                    "maxAge": max_age_ms,
                    "proxy": proxy,
                    "storeInCache": store_in_cache,
                },
            )
            resp.raise_for_status()

        body = resp.json()
        if body.get("success") is False:
            return FetchResult(
                url=url,
                content="",
                content_type="text/html",
                error=str(body.get("error") or body.get("message") or "Firecrawl scrape failed"),
            )

        data = body.get("data", {})
        if not isinstance(data, dict):
            data = {}
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        markdown = data.get("markdown") or data.get("content") or ""
        if not markdown:
            return FetchResult(
                url=metadata.get("sourceURL", url),
                content="",
                content_type="text/html",
                title=metadata.get("title", ""),
                status_code=metadata.get("statusCode", 200),
                error="Firecrawl scrape returned no content",
            )

        return FetchResult(
            url=metadata.get("sourceURL", url),
            content=markdown[:max_chars],
            content_type="text/html",
            title=metadata.get("title", ""),
            status_code=metadata.get("statusCode", 200),
            truncated=len(markdown) > max_chars,
            raw_length=len(markdown),
        )


def _resolve_endpoint(base_url: str) -> str:
    trimmed = base_url.strip()
    if not trimmed:
        return "https://api.firecrawl.dev/v2/scrape"
    try:
        parsed = httpx.URL(trimmed)
        if parsed.path and parsed.path != "/":
            return str(parsed)
        return str(parsed.copy_with(path="/v2/scrape"))
    except Exception:
        return "https://api.firecrawl.dev/v2/scrape"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        parsed = int(str(raw))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


__all__ = ["FirecrawlFetchBackend"]
