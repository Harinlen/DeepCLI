"""Tavily fetch backend — POST api.tavily.com/extract."""

from __future__ import annotations

import os

import httpx

from kernel.agents.mustang.tools.web.fetch_backends.base import FetchBackend, FetchResult


class TavilyFetchBackend(FetchBackend):
    """Tavily content extraction API."""

    name = "tavily"

    def is_available(self) -> bool:
        return bool(os.getenv("TAVILY_API_KEY", "").strip())

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.tavily.com/extract",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "urls": [url],
                    "include_images": False,
                },
            )
            if resp.status_code >= 400:
                return FetchResult(
                    url=url,
                    content="",
                    content_type="",
                    status_code=resp.status_code,
                    error=_format_tavily_error(resp),
                )

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


def _format_tavily_error(resp: httpx.Response) -> str:
    detail = ""
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            detail = str(
                payload.get("error")
                or payload.get("message")
                or payload.get("detail")
                or payload
            )
    except Exception:
        detail = resp.text.strip()
    hint = ""
    if resp.status_code == 401:
        hint = " Check that the API key is valid."
    elif resp.status_code == 429:
        hint = " Tavily rate limit or usage quota was exceeded."
    elif resp.status_code == 432:
        hint = " Tavily rejected the Extract request, commonly because the plan or endpoint access limit was exceeded."
    return f"Tavily Extract API returned HTTP {resp.status_code}: {detail or resp.reason_phrase}.{hint}"


__all__ = ["TavilyFetchBackend"]
