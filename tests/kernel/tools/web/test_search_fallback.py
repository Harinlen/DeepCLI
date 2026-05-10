"""Unit tests for search_with_fallback — mock backends."""

from __future__ import annotations

from typing import Any

import httpx

from kernel.agents.mustang.tools.web.search_backends import _has_env, get_available_backends, search_with_fallback
from kernel.agents.mustang.tools.web.search_backends.base import SearchBackend, SearchResult
from kernel.agents.mustang.tools.web.search_backends.duckduckgo import (
    DuckDuckGoSearchBackend,
    _parse_ddg_html,
    _resolve_ddg_url,
)


# ── Mock backend ──


class MockSearchBackend(SearchBackend):
    def __init__(
        self,
        name: str,
        *,
        results: list[SearchResult] | None = None,
        raise_exc: Exception | None = None,
    ):
        self.name = name
        self._results = results or []
        self._raise_exc = raise_exc

    def is_available(self) -> bool:
        return True

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        if self._raise_exc:
            raise self._raise_exc
        return self._results[:limit]


# ── Tests ──


async def test_first_available_wins():
    brave = MockSearchBackend(
        "brave",
        results=[SearchResult("Python", "https://python.org", "The language")],
    )
    ddg = MockSearchBackend(
        "duckduckgo",
        results=[SearchResult("Python", "https://python.org", "A language")],
    )
    results, name = await search_with_fallback("python", 10, backends=[brave, ddg])
    assert name == "brave"
    assert len(results) == 1


async def test_fallback_on_exception():
    fail = MockSearchBackend("brave", raise_exc=RuntimeError("rate limited"))
    ddg = MockSearchBackend(
        "duckduckgo",
        results=[SearchResult("Python", "https://python.org", "A language")],
    )
    results, name = await search_with_fallback("python", 10, backends=[fail, ddg])
    assert name == "duckduckgo"
    assert len(results) == 1


async def test_fallback_on_empty_results():
    empty = MockSearchBackend("brave", results=[])
    ddg = MockSearchBackend(
        "duckduckgo",
        results=[SearchResult("Python", "https://python.org", "A language")],
    )
    results, name = await search_with_fallback("python", 10, backends=[empty, ddg])
    assert name == "duckduckgo"


async def test_all_backends_fail():
    fail1 = MockSearchBackend("a", raise_exc=RuntimeError("x"))
    fail2 = MockSearchBackend("b", raise_exc=RuntimeError("y"))
    results, name = await search_with_fallback("python", 10, backends=[fail1, fail2])
    assert results == []
    assert "all backends failed" in name


async def test_preferred_backend():
    slow = MockSearchBackend(
        "slow",
        results=[SearchResult("Slow", "https://slow.com", "slow")],
    )
    fast = MockSearchBackend(
        "fast",
        results=[SearchResult("Fast", "https://fast.com", "fast")],
    )
    results, name = await search_with_fallback(
        "test", 10, backends=[slow, fast], preferred="fast"
    )
    assert name == "fast"


async def test_respects_limit():
    be = MockSearchBackend(
        "be",
        results=[
            SearchResult(f"R{i}", f"https://r{i}.com", f"s{i}")
            for i in range(20)
        ],
    )
    results, name = await search_with_fallback("test", 3, backends=[be])
    assert len(results) <= 3


def test_search_has_env_trims_empty_values(monkeypatch):
    monkeypatch.delenv("DEEPCLI_SEARCH_KEY", raising=False)
    assert _has_env("DEEPCLI_SEARCH_KEY") is False
    monkeypatch.setenv("DEEPCLI_SEARCH_KEY", "   ")
    assert _has_env("DEEPCLI_SEARCH_KEY") is False
    monkeypatch.setenv("DEEPCLI_SEARCH_KEY", " key ")
    assert _has_env("DEEPCLI_SEARCH_KEY") is True


def test_get_available_search_backends_always_includes_duckduckgo(monkeypatch):
    for key in (
        "BRAVE_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_CSE_ID",
        "EXA_API_KEY",
        "TAVILY_API_KEY",
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
        "PARALLEL_API_KEY",
        "PERPLEXITY_API_KEY",
        "KIMI_API_KEY",
        "MOONSHOT_API_KEY",
        "XAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    names = [backend.name for backend in get_available_backends()]

    assert names[-1] == "duckduckgo"


def test_duckduckgo_url_resolution_and_html_parsing():
    wrapped = (
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fa%3Fx%3D1"
    )
    html = f"""
    <a rel="nofollow" href="{wrapped}"> Example title </a>
    <td class="result-snippet">Snippet <b>text</b></td>
    <a rel="nofollow" href="/not-a-result">Ignored</a>
    <a rel="nofollow" href="https://direct.test">Direct</a>
    """

    assert _resolve_ddg_url(wrapped) == "https://example.test/a?x=1"
    assert _resolve_ddg_url("/not-a-result") is None
    results = _parse_ddg_html(html, limit=2)

    assert results == [
        SearchResult(
            title="Example title",
            url="https://example.test/a?x=1",
            snippet="Snippet text",
        ),
        SearchResult(title="Direct", url="https://direct.test", snippet=""),
    ]


async def test_duckduckgo_search_shapes_request_and_results(monkeypatch):
    html = """
    <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.test">Python</a>
    <td class="result-snippet">Language</td>
    """
    calls: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            calls.append({"url": url, **kwargs})
            return httpx.Response(
                200,
                text=html,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

    results = await DuckDuckGoSearchBackend().search("python", limit=1)

    assert calls[0]["url"] == "https://lite.duckduckgo.com/lite/"
    assert calls[0]["params"] == {"q": "python"}
    assert results == [SearchResult("Python", "https://python.test", "Language")]
