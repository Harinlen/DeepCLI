"""DuckDuckGo search backend — HTML scrape of lite UI.

Zero API key, always available. Last-resort fallback.

DDG lite returns results as ``<a rel="nofollow">`` links inside table
rows.  URLs are redirect wrappers (``//duckduckgo.com/l/?uddg=...``);
we extract the real URL from the ``uddg`` query parameter.  Snippets
follow in subsequent ``<td>`` cells with class ``result-snippet``.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from kernel.tools.web.search_backends.base import SearchBackend, SearchResult

_USER_AGENT = "deepcli/1.0"
_TIMEOUT_S = 10.0

# Matches result links: <a rel="nofollow" href="...">title</a>
# DDG lite wraps every result link with rel="nofollow".
_LINK_RE = re.compile(
    r'<a\s+rel="nofollow"\s+href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)

# Matches snippet cells that follow result links.
_SNIPPET_RE = re.compile(
    r'class="result-snippet"[^>]*>(.*?)</td>',
    re.IGNORECASE | re.DOTALL,
)


def _resolve_ddg_url(raw: str) -> str | None:
    """Extract the real URL from a DDG redirect wrapper.

    DDG lite links look like ``//duckduckgo.com/l/?uddg=https%3A%2F%2F...``
    Returns the decoded target URL, or None if not a result link.
    """
    # Normalise protocol-relative URLs
    if raw.startswith("//"):
        raw = "https:" + raw

    parsed = urlparse(raw)
    # DDG redirect links go through duckduckgo.com/l/
    if "duckduckgo.com" in (parsed.hostname or ""):
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)
        return None

    # Direct URL (rare in lite, but handle it)
    if raw.startswith("http"):
        return raw
    return None


def _parse_ddg_html(html: str, limit: int) -> list[SearchResult]:
    """Parse DuckDuckGo lite HTML into SearchResult list."""
    raw_links = _LINK_RE.findall(html)
    snippets = _SNIPPET_RE.findall(html)

    results: list[SearchResult] = []
    snippet_idx = 0
    for raw_url, title in raw_links:
        if len(results) >= limit:
            break
        url = _resolve_ddg_url(raw_url)
        if not url:
            continue

        snippet = ""
        if snippet_idx < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[snippet_idx]).strip()
            snippet_idx += 1

        results.append(
            SearchResult(
                title=title.strip(),
                url=url,
                snippet=snippet,
            )
        )
    return results


class DuckDuckGoSearchBackend(SearchBackend):
    """HTML scrape of DuckDuckGo lite — zero API key, always available."""

    name = "duckduckgo"

    def is_available(self) -> bool:
        return True

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
        return _parse_ddg_html(resp.text, limit)


__all__ = ["DuckDuckGoSearchBackend"]
