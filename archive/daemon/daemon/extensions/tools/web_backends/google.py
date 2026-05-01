"""Google Custom Search JSON API backend."""

from __future__ import annotations

from typing import Any

import httpx

from daemon.extensions.tools.web_backends.base import (
    SearchBackend,
    SearchResult,
    _TIMEOUT_SECS,
    _USER_AGENT,
)


class GoogleBackend(SearchBackend):
    """Google Custom Search JSON API backend."""

    name = "google"
    _ENDPOINT = "https://customsearch.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str, cse_id: str) -> None:
        self._api_key = api_key
        self._cse_id = cse_id

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        params: dict[str, Any] = {
            "q": query,
            "key": self._api_key,
            "cx": self._cse_id,
            "num": min(limit, 10),
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECS) as client:
            response = await client.get(self._ENDPOINT, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        items = data.get("items", []) or []
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
            )
            for item in items[:limit]
        ]

