"""Tests for WebSearchTool + backend selection (Step 5.4)."""

from __future__ import annotations

import pytest

from daemon.extensions.tools.base import ToolContext
from daemon.extensions.tools.builtin.web_search import WebSearchTool
from daemon.extensions.tools.web_backends import (
    BraveBackend,
    DuckDuckGoBackend,
    GoogleBackend,
    SearchBackend,
    SearchResult,
    select_backend,
)


class _FakeBackend(SearchBackend):
    name = "fake"

    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        assert limit > 0
        return self._results[:limit]


class TestSelectBackend:
    def test_brave_with_key(self) -> None:
        backend = select_backend("brave", "test-key")
        assert isinstance(backend, BraveBackend)

    def test_brave_without_key_returns_none(self) -> None:
        backend = select_backend("brave", None)
        assert backend is None

    def test_duckduckgo(self) -> None:
        backend = select_backend("duckduckgo", None)
        assert isinstance(backend, DuckDuckGoBackend)

    def test_google_with_keys(self) -> None:
        backend = select_backend(
            "google",
            None,
            google_api_key="google-key",
            google_cse_id="cse-id",
        )
        assert isinstance(backend, GoogleBackend)

    def test_google_without_cse_id_returns_none(self) -> None:
        backend = select_backend(
            "google",
            None,
            google_api_key="google-key",
            google_cse_id=None,
        )
        assert backend is None

    def test_auto_with_key(self) -> None:
        backend = select_backend(None, "test-key")
        assert isinstance(backend, DuckDuckGoBackend)

    def test_auto_prefers_ddg_when_only_google_keys_exist(self) -> None:
        backend = select_backend(
            None,
            None,
            google_api_key="google-key",
            google_cse_id="cse-id",
        )
        assert isinstance(backend, DuckDuckGoBackend)

    def test_auto_without_key_falls_back_to_ddg(self) -> None:
        backend = select_backend(None, None)
        assert isinstance(backend, DuckDuckGoBackend)

    def test_unknown_falls_back_to_auto(self) -> None:
        backend = select_backend("unknown-backend", "key")
        assert isinstance(backend, DuckDuckGoBackend)


class TestWebSearchTool:
    @pytest.fixture
    def ctx(self) -> ToolContext:
        return ToolContext(cwd="/tmp")

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self, ctx: ToolContext) -> None:
        backend = _FakeBackend(
            [
                SearchResult(title="Mustang", url="https://ex/1", snippet="snippet 1"),
                SearchResult(title="Horse", url="https://ex/2", snippet="snippet 2"),
            ]
        )
        tool = WebSearchTool(backend=backend)

        result = await tool.execute({"query": "mustang"}, ctx)
        assert result.is_error is False
        assert "1. Mustang" in result.output
        assert "https://ex/1" in result.output
        assert "2. Horse" in result.output

    @pytest.mark.asyncio
    async def test_respects_limit(self, ctx: ToolContext) -> None:
        backend = _FakeBackend(
            [SearchResult(title=f"t{i}", url=f"https://x/{i}", snippet="s") for i in range(10)]
        )
        tool = WebSearchTool(backend=backend)

        result = await tool.execute({"query": "q", "limit": 3}, ctx)
        # Should contain numeric prefixes 1, 2, 3 but not 4.
        assert "3. t2" in result.output
        assert "4." not in result.output.split("\n\n")[-1]

    @pytest.mark.asyncio
    async def test_no_results(self, ctx: ToolContext) -> None:
        tool = WebSearchTool(backend=_FakeBackend([]))
        result = await tool.execute({"query": "nothing"}, ctx)
        assert "No results" in result.output

    @pytest.mark.asyncio
    async def test_no_backend_available(
        self, ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        # Force the preferred backend to require a key we don't have.
        tool = WebSearchTool(preferred="brave")
        result = await tool.execute({"query": "q"}, ctx)
        assert result.is_error is True
        assert "No web_search backend" in result.output

    @pytest.mark.asyncio
    async def test_backend_raises(self, ctx: ToolContext) -> None:
        class _BoomBackend(SearchBackend):
            name = "boom"

            async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
                raise RuntimeError("kaboom")

        tool = WebSearchTool(backend=_BoomBackend())
        result = await tool.execute({"query": "q"}, ctx)
        assert result.is_error is True
        assert "kaboom" in result.output
