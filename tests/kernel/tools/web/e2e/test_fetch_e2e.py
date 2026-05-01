"""E2E tests for fetch backends — real network requests.

Run with: pytest tests/kernel/tools/web/e2e/ -m e2e -v
"""

from __future__ import annotations

import os

import pytest

from kernel.tools.web.fetch_backends import fetch_with_fallback
from kernel.tools.web.fetch_backends.httpx_html import HttpxFetchBackend

pytestmark = pytest.mark.e2e


# ── httpx backend (always available, CI must-run) ──


class TestHttpxFetchBackend:
    async def test_fetch_html_page(self):
        be = HttpxFetchBackend()
        result = await be.fetch("https://example.com")
        assert result.status_code == 200
        assert len(result.content) > 50
        assert "Example Domain" in result.content

    async def test_fetch_json_api(self):
        be = HttpxFetchBackend()
        result = await be.fetch("https://httpbin.org/json")
        assert result.status_code == 200
        assert "slideshow" in result.content

    async def test_fetch_respects_max_chars(self):
        be = HttpxFetchBackend()
        result = await be.fetch("https://example.com", max_chars=50)
        assert len(result.content) <= 50

    async def test_fetch_ssrf_blocked(self):
        be = HttpxFetchBackend()
        result = await be.fetch("http://169.254.169.254/latest/meta-data/")
        assert result.error is not None
        assert "Rejected" in result.error


# ── Firecrawl (needs key) ──


@pytest.mark.skipif(
    not os.getenv("FIRECRAWL_API_KEY", "").strip(),
    reason="FIRECRAWL_API_KEY not set",
)
class TestFirecrawlFetchBackend:
    async def test_fetch(self):
        from kernel.tools.web.fetch_backends.firecrawl import FirecrawlFetchBackend

        be = FirecrawlFetchBackend()
        result = await be.fetch("https://example.com")
        assert not result.error
        assert len(result.content) > 50


# ── Exa (needs key) ──


@pytest.mark.skipif(
    not os.getenv("EXA_API_KEY", "").strip(),
    reason="EXA_API_KEY not set",
)
class TestExaFetchBackend:
    async def test_fetch(self):
        from kernel.tools.web.fetch_backends.exa import ExaFetchBackend

        be = ExaFetchBackend()
        result = await be.fetch("https://docs.python.org/3/")
        assert not result.error
        assert len(result.content) > 50


# ── Tavily (needs key) ──


@pytest.mark.skipif(
    not os.getenv("TAVILY_API_KEY", "").strip(),
    reason="TAVILY_API_KEY not set",
)
class TestTavilyFetchBackend:
    async def test_fetch(self):
        from kernel.tools.web.fetch_backends.tavily import TavilyFetchBackend

        be = TavilyFetchBackend()
        result = await be.fetch("https://example.com")
        assert not result.error


# ── Parallel (needs key) ──


@pytest.mark.skipif(
    not os.getenv("PARALLEL_API_KEY", "").strip(),
    reason="PARALLEL_API_KEY not set",
)
class TestParallelFetchBackend:
    async def test_fetch(self):
        from kernel.tools.web.fetch_backends.parallel import ParallelFetchBackend

        be = ParallelFetchBackend()
        result = await be.fetch("https://example.com")
        assert not result.error


# ── Fallback chain integration ──


class TestFetchFallbackE2E:
    async def test_zero_config_uses_httpx(self, monkeypatch):
        for var in (
            "FIRECRAWL_API_KEY", "FIRECRAWL_API_URL",
            "PARALLEL_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        result, backend = await fetch_with_fallback("https://example.com")
        assert "Example Domain" in result.content
        # Should use a local backend (httpx or readability)
        assert "httpx" in backend or "readability" in backend

    async def test_preferred_backend(self):
        result, backend = await fetch_with_fallback(
            "https://example.com", preferred="httpx"
        )
        assert "httpx" in backend
        assert "Example Domain" in result.content
