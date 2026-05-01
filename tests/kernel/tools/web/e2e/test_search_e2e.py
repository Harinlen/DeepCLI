"""E2E tests for search backends — real network requests.

Run with: pytest tests/kernel/tools/web/e2e/ -m e2e -v
"""

from __future__ import annotations

import os

import pytest

from kernel.tools.web.search_backends import search_with_fallback
from kernel.tools.web.search_backends.duckduckgo import DuckDuckGoSearchBackend

pytestmark = pytest.mark.e2e


# ── DuckDuckGo (always available, CI must-run) ──


class TestDuckDuckGoSearchBackend:
    async def test_search_returns_results(self):
        be = DuckDuckGoSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1
        assert all(r.url.startswith("http") for r in results)
        assert all(r.title for r in results)

    async def test_search_respects_limit(self):
        be = DuckDuckGoSearchBackend()
        results = await be.search("python", limit=3)
        assert len(results) <= 3

    async def test_search_obscure_query(self):
        be = DuckDuckGoSearchBackend()
        results = await be.search("xyznonexistent12345qwerty", limit=5)
        assert isinstance(results, list)


# ── Brave (needs key) ──


@pytest.mark.skipif(
    not os.getenv("BRAVE_API_KEY", "").strip(),
    reason="BRAVE_API_KEY not set",
)
class TestBraveSearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.brave import BraveSearchBackend

        be = BraveSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1
        assert results[0].url.startswith("http")


# ── Google CSE (needs key) ──


@pytest.mark.skipif(
    not (os.getenv("GOOGLE_API_KEY", "").strip() and os.getenv("GOOGLE_CSE_ID", "").strip()),
    reason="GOOGLE_API_KEY or GOOGLE_CSE_ID not set",
)
class TestGoogleSearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.google import GoogleSearchBackend

        be = GoogleSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1


# ── Exa (needs key) ──


@pytest.mark.skipif(
    not os.getenv("EXA_API_KEY", "").strip(),
    reason="EXA_API_KEY not set",
)
class TestExaSearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.exa import ExaSearchBackend

        be = ExaSearchBackend()
        results = await be.search("python programming", limit=3)
        assert len(results) >= 1


# ── Tavily (needs key) ──


@pytest.mark.skipif(
    not os.getenv("TAVILY_API_KEY", "").strip(),
    reason="TAVILY_API_KEY not set",
)
class TestTavilySearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.tavily import TavilySearchBackend

        be = TavilySearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1


# ── Firecrawl (needs key) ──


@pytest.mark.skipif(
    not os.getenv("FIRECRAWL_API_KEY", "").strip(),
    reason="FIRECRAWL_API_KEY not set",
)
class TestFirecrawlSearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.firecrawl import FirecrawlSearchBackend

        be = FirecrawlSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1


# ── Parallel (needs key) ──


@pytest.mark.skipif(
    not os.getenv("PARALLEL_API_KEY", "").strip(),
    reason="PARALLEL_API_KEY not set",
)
class TestParallelSearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.parallel import ParallelSearchBackend

        be = ParallelSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1


# ── Perplexity (needs key) ──


@pytest.mark.skipif(
    not os.getenv("PERPLEXITY_API_KEY", "").strip(),
    reason="PERPLEXITY_API_KEY not set",
)
class TestPerplexitySearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.perplexity import PerplexitySearchBackend

        be = PerplexitySearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1


# ── Kimi (needs key) ──


@pytest.mark.skipif(
    not (os.getenv("KIMI_API_KEY", "").strip() or os.getenv("MOONSHOT_API_KEY", "").strip()),
    reason="KIMI_API_KEY not set",
)
class TestKimiSearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.kimi import KimiSearchBackend

        be = KimiSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1


# ── xAI (needs key) ──


@pytest.mark.skipif(
    not os.getenv("XAI_API_KEY", "").strip(),
    reason="XAI_API_KEY not set",
)
class TestXaiSearchBackend:
    async def test_search(self):
        from kernel.tools.web.search_backends.xai import XaiSearchBackend

        be = XaiSearchBackend()
        results = await be.search("python programming", limit=5)
        assert len(results) >= 1


# ── Fallback chain integration ──


class TestSearchFallbackE2E:
    async def test_zero_config_uses_duckduckgo(self, monkeypatch):
        for var in (
            "BRAVE_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CSE_ID",
            "EXA_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY",
            "FIRECRAWL_API_URL", "PARALLEL_API_KEY", "PERPLEXITY_API_KEY",
            "KIMI_API_KEY", "MOONSHOT_API_KEY", "XAI_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        results, backend = await search_with_fallback("python", 5)
        assert backend == "duckduckgo"
        assert len(results) >= 1

    async def test_preferred_backend(self):
        results, backend = await search_with_fallback(
            "python", 5, preferred="duckduckgo"
        )
        assert backend == "duckduckgo"
        assert len(results) >= 1
