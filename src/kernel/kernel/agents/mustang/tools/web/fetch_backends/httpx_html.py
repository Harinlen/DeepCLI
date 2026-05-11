"""httpx + html2text fetch backend — zero external dependency fallback.

Always available. Uses httpx for HTTP and html2text for HTML→Markdown.
This is the last-resort backend in the fallback chain.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

from kernel.agents.mustang.tools.web.domain_filter import check_domain
from kernel.agents.mustang.tools.web.fetch_backends.base import FetchBackend, FetchResult
from kernel.agents.mustang.tools.web.html_convert import html_to_markdown

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
_DEFAULT_HEADERS = {
    "Accept": "text/markdown, text/html;q=0.9, application/json;q=0.8, text/plain;q=0.7, */*;q=0.1",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": _DEFAULT_USER_AGENT,
}
_USER_AGENT_ENV = "DEEPCLI_WEB_FETCH_USER_AGENT"
_HEADERS = dict(_DEFAULT_HEADERS)
_TIMEOUT_S = 30.0
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_REDIRECTS = 10


def get_fetch_headers() -> dict[str, str]:
    headers = dict(_DEFAULT_HEADERS)
    user_agent = os.getenv(_USER_AGENT_ENV, "").strip()
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


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
                headers=get_fetch_headers(),
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

        # Byte cap: read enough for useful context without letting large
        # documents dominate the tool result.
        response_bytes = response.content
        body_bytes = response_bytes[:_MAX_BYTES]
        body_text = body_bytes.decode("utf-8", errors="replace")
        response_truncated = len(response_bytes) > _MAX_BYTES

        if response.status_code >= 400:
            error_detail = body_text[:4_000].strip()
            return FetchResult(
                url=final_url,
                content=error_detail,
                content_type=content_type,
                status_code=response.status_code,
                error=f"HTTP {response.status_code}: {response.reason_phrase}",
                truncated=response_truncated,
                raw_length=len(response_bytes),
            )

        title = ""
        if "text/markdown" in content_type:
            content = body_text[:max_chars]
        elif "json" in content_type:
            content = _format_json(body_text)[:max_chars]
        elif "xml" in content_type or "text/plain" in content_type:
            content = body_text[:max_chars]
        elif "html" in content_type or _looks_like_html(body_text):
            readable = _extract_readable_html(body_text, max_chars)
            if readable is not None:
                content, title = readable
            else:
                content = html_to_markdown(body_text, max_chars)
                title = ""
        elif _looks_like_binary(body_bytes, content_type):
            return FetchResult(
                url=final_url,
                content="",
                content_type=content_type,
                status_code=response.status_code,
                error=(
                    "Unsupported binary response. Use a browser, download tool, "
                    "or a specialised document/PDF reader for this URL."
                ),
                truncated=response_truncated,
                raw_length=len(response_bytes),
            )
        else:
            content = body_text[:max_chars]

        raw_text_length = len(body_text)
        content_truncated = response_truncated or raw_text_length > max_chars
        return FetchResult(
            url=final_url,
            content=content,
            content_type=content_type,
            title=title,
            status_code=response.status_code,
            truncated=content_truncated,
            raw_length=len(response_bytes),
        )


def _looks_like_html(value: str) -> bool:
    head = value.lstrip()[:256].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<body" in head


def _format_json(value: str) -> str:
    try:
        return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return value


def _extract_readable_html(html: str, max_chars: int) -> tuple[str, str] | None:
    """Use readability-lxml as an internal HTML extraction stage.

    Readability is not a WebFetch backend: it does not define a transport
    path.  It is a parser used after ``httpx`` has already fetched the
    document.
    """
    try:
        from readability import Document
    except Exception:
        return None
    try:
        doc = Document(html)
        summary = doc.summary()
        title = doc.title()
        content = html_to_markdown(summary, max_chars)
        return content, str(title or "")
    except Exception:
        logger.debug("readability extraction failed", exc_info=True)
        return None


def _looks_like_binary(value: bytes, content_type: str) -> bool:
    lower_type = content_type.lower()
    if lower_type.startswith(("image/", "audio/", "video/")):
        return True
    if "application/octet-stream" in lower_type or "application/pdf" in lower_type:
        return True
    return b"\x00" in value[:1024]


__all__ = ["HttpxFetchBackend", "_HEADERS", "_extract_readable_html", "get_fetch_headers"]
