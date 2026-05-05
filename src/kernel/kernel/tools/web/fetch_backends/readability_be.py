"""Readability fetch backend — Mozilla Readability via readability-lxml.

Optional local dependency, no API key needed.
"""

from __future__ import annotations

import httpx

from kernel.tools.web.domain_filter import check_domain
from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult
from kernel.tools.web.fetch_backends.httpx_html import get_fetch_headers, _send_with_redirect_check
from kernel.tools.web.html_convert import html_to_markdown

_TIMEOUT_S = 30.0
_MAX_REDIRECTS = 10


class ReadabilityFetchBackend(FetchBackend):
    """Extract main content via readability-lxml + html2text."""

    name = "readability"

    def is_available(self) -> bool:
        try:
            import readability  # type: ignore[import-not-found]  # noqa: F401

            return True
        except ImportError:
            return False

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        from readability import Document

        if err := check_domain(url):
            return FetchResult(url=url, content="", content_type="", error=err)

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT_S,
                follow_redirects=False,
                max_redirects=_MAX_REDIRECTS,
                headers=get_fetch_headers(),
            ) as client:
                response, final_url = await _send_with_redirect_check(
                    client,
                    url,
                    max_redirects=_MAX_REDIRECTS,
                )
        except httpx.HTTPError as exc:
            return FetchResult(
                url=url,
                content="",
                content_type="",
                error=f"HTTP error: {exc}",
            )

        if response.status_code >= 400:
            return FetchResult(
                url=final_url,
                content=response.text[:4_000],
                content_type=response.headers.get("content-type", ""),
                status_code=response.status_code,
                error=f"HTTP {response.status_code}: {response.reason_phrase}",
            )

        doc = Document(response.text)
        html_content = doc.summary()
        title = doc.title()
        markdown = html_to_markdown(html_content, max_chars)

        return FetchResult(
            url=final_url,
            content=markdown,
            content_type=response.headers.get("content-type", ""),
            title=title,
            status_code=response.status_code,
        )


__all__ = ["ReadabilityFetchBackend"]
