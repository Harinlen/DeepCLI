"""Fetch backend registry and fallback chain.

``fetch_with_fallback`` tries each available backend in priority order,
falling back on failure, anti-bot detection, or empty content.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_ANTI_BOT_MARKERS = (
    "captcha",
    "cloudflare",
    "challenge",
    "just a moment",
    "verify you are human",
    "access denied",
)


def _has_env(name: str) -> bool:
    val = os.getenv(name)
    return bool(val and val.strip())


def _looks_like_anti_bot(result: FetchResult) -> bool:
    """Detect anti-bot / captcha empty pages.

    Only triggers on truly empty responses or known captcha patterns
    with error status codes. Short legitimate pages (e.g. example.com
    at ~170 chars) must NOT be flagged.
    """
    content = result.content or ""
    stripped = content.strip()

    # Truly empty — no content at all
    if len(stripped) < 50:
        return True

    # Known anti-bot patterns with error status codes
    if result.status_code in (403, 429, 503):
        lower = content.lower()
        return any(m in lower for m in _ANTI_BOT_MARKERS)

    return False


def get_available_backends() -> list[FetchBackend]:
    """Return currently-available backend instances in priority order."""
    from kernel.tools.web.fetch_backends.exa import ExaFetchBackend
    from kernel.tools.web.fetch_backends.firecrawl import FirecrawlFetchBackend
    from kernel.tools.web.fetch_backends.httpx_html import HttpxFetchBackend
    from kernel.tools.web.fetch_backends.parallel import ParallelFetchBackend
    from kernel.tools.web.fetch_backends.playwright_be import PlaywrightFetchBackend
    from kernel.tools.web.fetch_backends.readability_be import ReadabilityFetchBackend
    from kernel.tools.web.fetch_backends.tavily import TavilyFetchBackend

    priority: list[type[FetchBackend]] = [
        FirecrawlFetchBackend,
        ParallelFetchBackend,
        ExaFetchBackend,
        TavilyFetchBackend,
        ReadabilityFetchBackend,
        PlaywrightFetchBackend,
        HttpxFetchBackend,  # always available
    ]
    return [cls() for cls in priority if cls().is_available()]


async def fetch_with_fallback(
    url: str,
    *,
    max_chars: int = 50_000,
    preferred: str | None = None,
    backends: list[FetchBackend] | None = None,
) -> tuple[FetchResult, str]:
    """Try each backend in order; return (result, backend_name).

    If *preferred* is set, that backend is tried first.
    If *backends* is provided, use that list instead of auto-detecting.
    """
    if backends is None:
        backends = get_available_backends()

    if preferred:
        backends = sorted(backends, key=lambda b: 0 if b.name == preferred else 1)

    errors: list[str] = []
    httpx_result: FetchResult | None = None

    for backend in backends:
        try:
            result = await backend.fetch(url, max_chars=max_chars)
            if result.error:
                errors.append(f"{backend.name}: {result.error}")
                if backend.name == "httpx":
                    httpx_result = result
                continue

            if _looks_like_anti_bot(result):
                errors.append(f"{backend.name}: anti-bot page detected")
                if backend.name == "httpx":
                    httpx_result = result
                continue

            return result, backend.name

        except Exception as exc:
            errors.append(f"{backend.name}: {exc}")
            continue

    # All failed — return httpx result if we have one, else error
    if httpx_result:
        return httpx_result, f"httpx (fallback, errors: {'; '.join(errors)})"
    return FetchResult(
        url=url,
        content="",
        content_type="",
        error=f"All backends failed: {'; '.join(errors)}",
    ), "none"


__all__ = [
    "FetchBackend",
    "FetchResult",
    "fetch_with_fallback",
    "get_available_backends",
]
