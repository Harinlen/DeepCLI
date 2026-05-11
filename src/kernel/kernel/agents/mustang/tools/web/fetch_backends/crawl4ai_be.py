"""Crawl4AI WebFetch backend."""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

from kernel.agents.mustang.tools.web.domain_filter import check_domain
from kernel.agents.mustang.tools.web.fetch_backends.base import FetchBackend, FetchResult
from kernel.agents.mustang.tools.web.html_convert import html_to_markdown
from kernel.agents.mustang.tools.web.management import (
    _activate_python_paths,
    _deepcli_package_dir,
    _deepcli_setup_env,
)

logger = logging.getLogger(__name__)


def _apply_crawl4ai_env() -> None:
    _activate_python_paths((str(_deepcli_package_dir("crawl4ai")),))
    for key, value in _deepcli_setup_env().items():
        os.environ.setdefault(key, value)
        Path(value).mkdir(parents=True, exist_ok=True)


class Crawl4AIFetchBackend(FetchBackend):
    """Local browser-rendering backend powered by Crawl4AI."""

    name = "crawl4ai"

    def is_available(self) -> bool:
        _apply_crawl4ai_env()
        return importlib.util.find_spec("crawl4ai") is not None

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        if err := check_domain(url):
            return FetchResult(url=url, content="", content_type="", error=err)
        try:
            _apply_crawl4ai_env()
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
        except Exception as exc:
            return FetchResult(
                url=url,
                content="",
                content_type="",
                error=f"Crawl4AI is not installed or failed to import: {exc}",
            )

        try:
            browser_config = BrowserConfig(
                headless=True,
                verbose=False,
                browser_type="chromium",
                ignore_https_errors=True,
                java_script_enabled=True,
            )
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.ENABLED,
                word_count_threshold=10,
                process_iframes=True,
                remove_overlay_elements=True,
                excluded_tags=["script", "style"],
                page_timeout=30_000,
                verbose=False,
                wait_until="domcontentloaded",
            )
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=url, config=run_config)
        except Exception as exc:
            return FetchResult(
                url=url,
                content="",
                content_type="text/markdown",
                error=f"Crawl4AI error: {exc}",
            )

        if not getattr(result, "success", False):
            message = getattr(result, "error_message", "unknown Crawl4AI failure")
            return FetchResult(url=url, content="", content_type="text/markdown", error=message)

        markdown = str(getattr(result, "markdown", "") or "")
        if not markdown.strip():
            html = str(getattr(result, "cleaned_html", None) or getattr(result, "html", None) or "")
            if html:
                markdown = html_to_markdown(html, max_chars)
        metadata = getattr(result, "metadata", None) or {}
        title = metadata.get("title") if isinstance(metadata, dict) else ""
        final_url = str(getattr(result, "url", url) or url)
        content = markdown[:max_chars]
        return FetchResult(
            url=final_url,
            content=content,
            content_type="text/markdown",
            title=str(title or ""),
            status_code=int(getattr(result, "status_code", 200) or 200),
            truncated=len(markdown) > max_chars,
            raw_length=len(markdown),
        )


__all__ = ["Crawl4AIFetchBackend"]
