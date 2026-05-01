"""HttpFetchTool — clean HTTP client for machine-to-machine endpoints.

A thin wrapper around ``httpx`` exposing all common HTTP methods,
arbitrary headers and request bodies.  Returns the **raw response
body** verbatim — no HTML parsing, no readability extraction.

This tool is intentionally narrow: use it for JSON APIs, REST,
GraphQL, RSS, raw text files, webhook tests, file downloads.  For
reading human-facing web pages (which usually need JavaScript), use
``page_fetch`` or ``browser`` instead.

Security:
  * Rejects non-http(s) schemes.
  * Reuses :func:`daemon.extensions.tools.domain_filter.check_domain`
    to block SSRF (loopback, link-local, private IPs, operator
    blocklist).  Each redirect hop is re-checked.
  * 5 MB hard cap on the response body.
  * Permission level: ``PROMPT`` — outbound network egress should be
    explicit.
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.extensions.tools.domain_filter import check_domain

logger = logging.getLogger(__name__)

_USER_AGENT = "mustang-http-fetch/1.0"
_TIMEOUT_SECS = 30.0
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_MAX_REDIRECTS = 10

# Headers we surface to the LLM (everything else is hidden — most
# headers are noise; ``Set-Cookie`` is PII).
_VISIBLE_HEADERS = frozenset({
    "content-type",
    "content-length",
    "location",
    "server",
})


def _format_headers(headers: httpx.Headers) -> str:
    """Render the response headers we want the LLM to see."""
    lines: list[str] = []
    for name, value in headers.items():
        lower = name.lower()
        if lower in _VISIBLE_HEADERS or lower.startswith("x-"):
            lines.append(f"{name}: {value}")
    return "\n".join(lines)


class HttpFetchTool(Tool):
    """Make an HTTP request, return the raw response body."""

    name = "http_fetch"
    description = (
        "Make an HTTP request to a **machine API endpoint** and return the "
        "raw response body. Supports GET, POST, PUT, PATCH, DELETE, HEAD, "
        "and OPTIONS, with custom headers and request bodies. Returns the "
        "response body verbatim — no HTML parsing, no readability extraction.\n\n"
        "## When to use http_fetch\n\n"
        "**ONLY use this tool when the URL is unambiguously a machine "
        "endpoint**, not a human-facing web page. Concrete signals:\n\n"
        "- Hostname starts with `api.` (e.g. `api.github.com`, `api.openai.com`)\n"
        "- Hostname is a CDN or raw-content host (`raw.githubusercontent.com`, "
        "`cdn.example.com`, `assets.example.com`)\n"
        "- Path starts with `/api/`, `/v1/`, `/v2/`, `/rest/`, `/graphql`\n"
        "- The endpoint is documented to return JSON / XML / RSS / plain text\n"
        "- You're testing a webhook, downloading a file, or hitting a known\n"
        "  REST/GraphQL endpoint\n\n"
        "## When NOT to use http_fetch\n\n"
        "**For ANYTHING a human would open in a browser**, use `page_fetch` "
        "instead — even if the page seems static / server-rendered / has no "
        "JavaScript. Examples where you MUST use page_fetch, not http_fetch:\n\n"
        "- News articles, blog posts, documentation sites\n"
        "- GitHub repository pages, READMEs viewed in the browser\n"
        "- Wikipedia, Stack Overflow, search engine results\n"
        "- Government / institutional pages (weather services, statistics, etc.)\n"
        "- Dashboards, social media profiles, product pages\n"
        "- Any URL ending in `.html`, `.shtml`, `.htm`, or with no extension\n\n"
        "Even server-rendered HTML pages return cluttered markup full of "
        "navigation and chrome — page_fetch returns the **structured "
        "content** the user actually wants. **When in doubt, use page_fetch.**\n\n"
        "For multi-step browsing, screenshots, or capturing XHR network "
        "traffic, use the `browser` tool instead."
    )
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.PARALLEL

    class Input(BaseModel):
        url: str = Field(description="HTTP or HTTPS URL.")
        method: Literal[
            "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"
        ] = Field(default="GET", description="HTTP method.")
        headers: dict[str, str] | None = Field(
            default=None,
            description="Optional request headers (e.g. Authorization, Content-Type).",
        )
        body: str | None = Field(
            default=None,
            description=(
                "Request body as a string. For JSON, set "
                "Content-Type: application/json in headers and pass a "
                "JSON-stringified payload here."
            ),
        )
        max_chars: int = Field(
            default=50_000,
            gt=0,
            description="Maximum characters of response body to return.",
        )
        follow_redirects: bool = Field(
            default=True,
            description="Follow 3xx redirects (max 10 hops).",
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        validated = self.Input.model_validate(params)

        # --- URL validation ---
        parsed = urlparse(validated.url)
        if parsed.scheme not in {"http", "https"}:
            return ToolResult(
                output=f"Rejected: only http(s) URLs allowed, got {parsed.scheme!r}",
                is_error=True,
            )
        if not parsed.netloc:
            return ToolResult(output="Rejected: URL missing host", is_error=True)
        if domain_err := check_domain(validated.url):
            return ToolResult(output=domain_err, is_error=True)

        # --- Build request headers ---
        request_headers: dict[str, str] = {"User-Agent": _USER_AGENT}
        if validated.headers:
            request_headers.update(validated.headers)

        timeout = httpx.Timeout(timeout=_TIMEOUT_SECS, connect=10.0, read=_TIMEOUT_SECS)

        # --- Send request, manually following redirects ---
        try:
            response, final_url = await _send(
                method=validated.method,
                url=validated.url,
                headers=request_headers,
                body=validated.body,
                timeout=timeout,
                follow_redirects=validated.follow_redirects,
            )
        except httpx.TimeoutException as exc:
            return ToolResult(
                output=f"HTTP timeout after {_TIMEOUT_SECS:.0f}s ({exc.__class__.__name__})",
                is_error=True,
            )
        except httpx.HTTPStatusError as exc:
            # Raised by us when a redirect lands on a blocked domain.
            return ToolResult(output=str(exc), is_error=True)
        except httpx.HTTPError as exc:
            return ToolResult(
                output=f"HTTP error ({exc.__class__.__name__}): {exc!r}",
                is_error=True,
            )

        # --- Read body with byte cap ---
        body_bytes = response.content[:_MAX_BYTES]
        body_truncated_by_bytes = len(response.content) > _MAX_BYTES

        body_text = body_bytes.decode("utf-8", errors="replace")
        body_truncated_by_chars = False
        if len(body_text) > validated.max_chars:
            body_text = body_text[: validated.max_chars]
            body_truncated_by_chars = True

        truncation_marker = ""
        if body_truncated_by_chars or body_truncated_by_bytes:
            reason = (
                f"truncated at {validated.max_chars} chars"
                if body_truncated_by_chars
                else f"truncated at {_MAX_BYTES // (1024 * 1024)} MB"
            )
            truncation_marker = f"\n\n... ({reason})"

        # --- Format output: optional redirect note + status line +
        # headers + blank + body ---
        status_line = f"HTTP/1.1 {response.status_code} {response.reason_phrase}"
        header_block = _format_headers(response.headers)

        output_parts: list[str] = []
        if final_url != validated.url:
            output_parts.append(f"[redirected: {validated.url} → {final_url}]")
        output_parts.append(status_line)
        if header_block:
            output_parts.append(header_block)
        output_parts.append("")  # blank line before body
        output_parts.append(body_text + truncation_marker)
        output = "\n".join(output_parts)

        return ToolResult(
            output=output,
            is_error=response.status_code >= 400,
        )


async def _send(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: str | None,
    timeout: httpx.Timeout,
    follow_redirects: bool,
) -> tuple[httpx.Response, str]:
    """Send an HTTP request, manually following redirects (with SSRF re-check).

    Returns the final response plus the final URL after any hops.
    """
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers=headers,
    ) as client:
        current_url = url
        for _ in range(_MAX_REDIRECTS + 1):
            response = await client.request(method, current_url, content=body)
            if not follow_redirects or not response.is_redirect:
                return response, current_url
            location = response.headers.get("location", "")
            if not location:
                return response, current_url
            next_url = str(response.url.join(location))
            if domain_err := check_domain(next_url):
                raise httpx.HTTPStatusError(
                    f"Redirect blocked: {current_url} → {next_url}: {domain_err}",
                    request=response.request,
                    response=response,
                )
            current_url = next_url
        # Too many redirects.
        raise httpx.TooManyRedirects(
            f"Exceeded {_MAX_REDIRECTS} redirects starting from {url}",
            request=response.request,
        )


__all__ = ["HttpFetchTool"]
