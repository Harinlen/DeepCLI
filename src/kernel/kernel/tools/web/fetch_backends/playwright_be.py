"""Playwright fetch backend — headless Chrome.

Optional local dependency. Handles JS-heavy pages that httpx can't render.
"""

from __future__ import annotations

from kernel.tools.web.domain_filter import check_domain
from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult


class PlaywrightFetchBackend(FetchBackend):
    """Headless Chrome via Playwright."""

    name = "playwright"

    def is_available(self) -> bool:
        try:
            import playwright  # type: ignore[import-not-found]  # noqa: F401

            return True
        except ImportError:
            return False

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        if err := check_domain(url):
            return FetchResult(url=url, content="", content_type="", error=err)

        from playwright.async_api import async_playwright  # type: ignore[import-not-found]

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)
                title = await page.title()
                content = await page.evaluate("document.body.innerText") or ""
                return FetchResult(
                    url=page.url,
                    content=content[:max_chars],
                    content_type="text/html",
                    title=title,
                )
            finally:
                await browser.close()


__all__ = ["PlaywrightFetchBackend"]
