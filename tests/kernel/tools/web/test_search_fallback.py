"""Unit tests for search_with_fallback — mock backends."""

from __future__ import annotations


from kernel.tools.web.search_backends import search_with_fallback
from kernel.tools.web.search_backends.base import SearchBackend, SearchResult


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
