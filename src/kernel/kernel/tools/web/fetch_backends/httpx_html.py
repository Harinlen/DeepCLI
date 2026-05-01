"""httpx + html2text fetch backend — zero external dependency fallback.

Always available. Uses httpx for HTTP and html2text for HTML→Markdown.
This is the last-resort backend in the fallback chain.
"""

from __future__ import annotations

import logging

import httpx

from kernel.tools.web.domain_filter import check_domain
from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult
from kernel.tools.web.html_convert import html_to_markdown

logger = logging.getLogger(__name__)

_USER_AGENT = "deepcli/1.0"
_TIMEOUT_S = 30.0
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_REDIRECTS = 10


async def _send_with_redirect_check(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_redirects: int = _MAX_REDIRECTS,
) -> tuple[httpx.Response, str]:
    """Send GET, manually following redirects with SSRF re-check."""
    current_url = url
    for _ in range(max_redirects + 1):
        response = await client.request("GET", current_url)
        if not response.is_redirect:
            return response, current_url
        location = response.headers.get("location", "")
        if not location:
            return response, current_url
        next_url = str(response.url.join(location))
        if err := check_domain(next_url):
            raise httpx.HTTPStatusError(
                f"Redirect blocked: {current_url} → {next_url}: {err}",
                request=response.request,
                response=response,
            )
        current_url = next_url
    raise httpx.TooManyRedirects(
        f"Exceeded {max_redirects} redirects from {url}",
        request=response.request,  # type: ignore[possibly-undefined]
    )


class HttpxFetchBackend(FetchBackend):
    """Zero-dependency fallback: httpx GET + html2text."""

    name = "httpx"

    def is_available(self) -> bool:
        return True

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        # SSRF check on initial URL
        if err := check_domain(url):
            return FetchResult(url=url, content="", content_type="", error=err)

        # Auto-upgrade http → https
        if url.startswith("http://"):
            url = "https://" + url[7:]

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT_S,
                follow_redirects=False,
                headers={"User-Agent": _USER_AGENT},
                max_redirects=0,
            ) as client:
                response, final_url = await _send_with_redirect_check(client, url)
        except httpx.TimeoutException:
            return FetchResult(
                url=url,
                content="",
                content_type="",
                error=f"HTTP timeout after {_TIMEOUT_S:.0f}s",
            )
        except httpx.HTTPError as exc:
            return FetchResult(
                url=url,
                content="",
                content_type="",
                error=f"HTTP error: {exc}",
            )

        content_type = response.headers.get("content-type", "")

        # Byte cap
        body_bytes = response.content[:_MAX_BYTES]
        body_text = body_bytes.decode("utf-8", errors="replace")

        if "json" in content_type or "xml" in content_type or "text/plain" in content_type:
            content = body_text[:max_chars]
        elif "html" in content_type:
            content = html_to_markdown(body_text, max_chars)
        else:
            content = body_text[:max_chars]

        return FetchResult(
            url=final_url,
            content=content,
            content_type=content_type,
            status_code=response.status_code,
        )


__all__ = ["HttpxFetchBackend"]
