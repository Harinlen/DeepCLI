"""DuckDuckGo HTML backend."""

from __future__ import annotations

import httpx

from daemon.extensions.tools.web_backends.base import (
    SearchBackend,
    SearchResult,
    _TIMEOUT_SECS,
    _USER_AGENT,
)


class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo HTML scrape backend."""

    name = "duckduckgo"
    _ENDPOINT = "https://html.duckduckgo.com/html/"

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        from bs4 import BeautifulSoup

        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECS) as client:
            response = await client.post(self._ENDPOINT, headers=headers, data={"q": query})
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        results: list[SearchResult] = []
        for block in soup.select("div.result")[:limit]:
            title_el = block.select_one("a.result__a")
            snippet_el = block.select_one("a.result__snippet, div.result__snippet")
            if title_el is None:
                continue
            href = title_el.get("href", "") or ""
            results.append(
                SearchResult(
                    title=title_el.get_text(strip=True),
                    url=href if isinstance(href, str) else "",
                    snippet=snippet_el.get_text(strip=True) if snippet_el else "",
                )
            )
        return results

