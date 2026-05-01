"""Exa fetch backend — POST api.exa.ai/search with contents.text.

Direct REST API call, no exa-py SDK. Verified by OpenClaw's impl.
"""

from __future__ import annotations

import os

import httpx

from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult


class ExaFetchBackend(FetchBackend):
    """Exa semantic content extraction via search endpoint."""

    name = "exa"

    def is_available(self) -> bool:
        return bool(os.getenv("EXA_API_KEY", "").strip())

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": os.getenv("EXA_API_KEY", ""),
                    "Content-Type": "application/json",
                },
                json={
                    "query": url,
                    "numResults": 1,
                    "contents": {"text": True},
                },
            )
            resp.raise_for_status()

        results = resp.json().get("results", [])
        if not results:
            return FetchResult(
                url=url,
                content="",
                content_type="",
                error="no results from Exa",
            )
        r = results[0]
        return FetchResult(
            url=r.get("url", url),
            content=(r.get("text") or "")[:max_chars],
            content_type="text/html",
            title=r.get("title", ""),
        )


__all__ = ["ExaFetchBackend"]
