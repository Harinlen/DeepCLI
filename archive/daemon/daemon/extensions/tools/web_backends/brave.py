"""Brave Search API backend."""

from __future__ import annotations

from typing import Any

import httpx

from daemon.extensions.tools.web_backends.base import (
    SearchBackend,
    SearchResult,
    _TIMEOUT_SECS,
    _USER_AGENT,
)


class BraveBackend(SearchBackend):
    """Brave Search API — requires ``BRAVE_API_KEY``."""

    name = "brave"
    _ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._api_key,
            "User-Agent": _USER_AGENT,
        }
        params: dict[str, Any] = {"q": query, "count": limit}
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECS) as client:
            response = await client.get(self._ENDPOINT, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        items = data.get("web", {}).get("results", []) or []
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
            )
            for item in items[:limit]
        ]

