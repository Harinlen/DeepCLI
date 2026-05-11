from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from kernel.agents.mustang.tools.web.fetch_backends.exa import ExaFetchBackend
from kernel.agents.mustang.tools.web.fetch_backends.firecrawl import FirecrawlFetchBackend
from kernel.agents.mustang.tools.web.fetch_backends.parallel import ParallelFetchBackend
from kernel.agents.mustang.tools.web.fetch_backends.tavily import TavilyFetchBackend
from kernel.agents.mustang.tools.web.search_backends.brave import BraveSearchBackend
from kernel.agents.mustang.tools.web.search_backends.exa import ExaSearchBackend
from kernel.agents.mustang.tools.web.search_backends.firecrawl import FirecrawlSearchBackend
from kernel.agents.mustang.tools.web.search_backends.google import GoogleSearchBackend
from kernel.agents.mustang.tools.web.search_backends.kimi import KimiSearchBackend
from kernel.agents.mustang.tools.web.search_backends.parallel import ParallelSearchBackend
from kernel.agents.mustang.tools.web.search_backends.perplexity import PerplexitySearchBackend
from kernel.agents.mustang.tools.web.search_backends.tavily import TavilySearchBackend
from kernel.agents.mustang.tools.web.search_backends.xai import XaiSearchBackend


class _FakeAsyncClient:
    def __init__(
        self,
        *,
        handler: Callable[[str, str, dict[str, Any]], httpx.Response],
        timeout: int | float,
    ) -> None:
        self._handler = handler
        self.timeout = timeout

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._handler("POST", url, kwargs)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._handler("GET", url, kwargs)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[str, str, dict[str, Any]], httpx.Response],
) -> list[tuple[str, str, dict[str, Any]]]:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def recording(method: str, url: str, kwargs: dict[str, Any]) -> httpx.Response:
        calls.append((method, url, kwargs))
        return handler(method, url, kwargs)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *, timeout: _FakeAsyncClient(handler=recording, timeout=timeout),
    )
    return calls


def _json_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("GET", "https://example.test"))


@pytest.mark.parametrize(
    ("env_name", "backend"),
    [
        ("EXA_API_KEY", ExaFetchBackend()),
        ("TAVILY_API_KEY", TavilyFetchBackend()),
        ("PARALLEL_API_KEY", ParallelFetchBackend()),
    ],
)
def test_fetch_backend_availability_uses_api_key(
    env_name: str,
    backend: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(env_name, raising=False)
    assert backend.is_available() is False
    monkeypatch.setenv(env_name, " key ")
    assert backend.is_available() is True


def test_firecrawl_availability_accepts_key_or_custom_url(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FirecrawlFetchBackend()
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_URL", raising=False)
    assert backend.is_available() is False
    monkeypatch.setenv("FIRECRAWL_API_URL", "http://localhost:3002")
    assert backend.is_available() is True


async def test_exa_fetch_success_and_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    responses = [
        _json_response(
            {"results": [{"url": "https://page.test", "text": "abcdef", "title": "Title"}]}
        ),
        _json_response({"results": []}),
    ]
    calls = _patch_client(monkeypatch, lambda *_: responses.pop(0))
    backend = ExaFetchBackend()

    result = await backend.fetch("https://page.test", max_chars=3)
    empty = await backend.fetch("https://missing.test")

    assert calls[0][1] == "https://api.exa.ai/search"
    assert calls[0][2]["headers"]["x-api-key"] == "exa-key"
    assert result.content == "abc"
    assert result.title == "Title"
    assert empty.error == "no results from Exa"


async def test_firecrawl_fetch_uses_custom_base_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setenv("FIRECRAWL_API_URL", "http://firecrawl.local/")
    monkeypatch.setenv("FIRECRAWL_MAX_AGE_MS", "123")
    monkeypatch.setenv("FIRECRAWL_PROXY", "stealth")
    calls = _patch_client(
        monkeypatch,
        lambda *_: _json_response(
            {
                "data": {
                    "markdown": "hello world",
                    "metadata": {
                        "sourceURL": "https://final.test",
                        "title": "Final",
                        "statusCode": 201,
                    },
                }
            }
        ),
    )

    result = await FirecrawlFetchBackend().fetch("https://source.test", max_chars=5)

    assert calls[0][1] == "http://firecrawl.local/v2/scrape"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer fc-key"
    assert calls[0][2]["json"]["maxAge"] == 123
    assert calls[0][2]["json"]["proxy"] == "stealth"
    assert result.url == "https://final.test"
    assert result.content == "hello"
    assert result.truncated is True
    assert result.raw_length == len("hello world")
    assert result.status_code == 201


async def test_firecrawl_fetch_accepts_endpoint_url_and_failed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_URL", "http://firecrawl.local/custom/scrape")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    calls = _patch_client(
        monkeypatch,
        lambda *_: _json_response({"success": False, "error": "blocked upstream"}),
    )

    result = await FirecrawlFetchBackend().fetch("https://source.test")

    assert calls[0][1] == "http://firecrawl.local/custom/scrape"
    assert "Authorization" not in calls[0][2]["headers"]
    assert result.error == "blocked upstream"


async def test_parallel_and_tavily_fetch_empty_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        _json_response(
            {"results": [{"url": "https://parallel.test", "full_content": "parallel", "title": "P"}]}
        ),
        _json_response({"results": []}),
        _json_response(
            {"results": [{"url": "https://tavily.test", "content": "tavily", "title": "T"}]}
        ),
        _json_response({"results": []}),
    ]
    _patch_client(monkeypatch, lambda *_: responses.pop(0))

    assert (await ParallelFetchBackend().fetch("https://parallel.test")).content == "parallel"
    assert (await ParallelFetchBackend().fetch("https://empty.test")).error == "no results from Parallel"
    assert (await TavilyFetchBackend().fetch("https://tavily.test")).content == "tavily"
    assert (await TavilyFetchBackend().fetch("https://empty.test")).error == "no results from Tavily"


async def test_tavily_fetch_uses_bearer_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    calls = _patch_client(monkeypatch, lambda *_: _json_response({"results": []}))

    await TavilyFetchBackend().fetch("https://tavily.test")

    assert calls[0][1] == "https://api.tavily.com/extract"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer tvly-test"
    assert "api_key" not in calls[0][2]["json"]


async def test_tavily_fetch_surfaces_432_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(
        monkeypatch,
        lambda *_: httpx.Response(
            432,
            json={"error": "Plan Limit Exceeded"},
            request=httpx.Request("POST", "https://api.tavily.com/extract"),
        ),
    )

    result = await TavilyFetchBackend().fetch("https://page.test")

    assert result.status_code == 432
    assert result.error is not None
    assert "Plan Limit Exceeded" in result.error
    assert "Extract" in result.error


@pytest.mark.parametrize(
    ("env_name", "backend"),
    [
        ("BRAVE_API_KEY", BraveSearchBackend()),
        ("EXA_API_KEY", ExaSearchBackend()),
        ("PERPLEXITY_API_KEY", PerplexitySearchBackend()),
        ("PARALLEL_API_KEY", ParallelSearchBackend()),
        ("TAVILY_API_KEY", TavilySearchBackend()),
        ("XAI_API_KEY", XaiSearchBackend()),
    ],
)
def test_search_backend_availability_uses_api_key(
    env_name: str,
    backend: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(env_name, raising=False)
    assert backend.is_available() is False
    monkeypatch.setenv(env_name, " key ")
    assert backend.is_available() is True


def test_kimi_availability_accepts_either_key(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = KimiSearchBackend()
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    assert backend.is_available() is False
    monkeypatch.setenv("MOONSHOT_API_KEY", "moon")
    assert backend.is_available() is True


def test_google_availability_requires_key_and_cse(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = GoogleSearchBackend()
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_ID", raising=False)
    assert backend.is_available() is False
    monkeypatch.setenv("GOOGLE_API_KEY", "google")
    assert backend.is_available() is False
    monkeypatch.setenv("GOOGLE_CSE_ID", "cse")
    assert backend.is_available() is True


def test_firecrawl_search_availability_accepts_key_or_custom_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FirecrawlSearchBackend()
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_URL", raising=False)
    assert backend.is_available() is False
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc")
    assert backend.is_available() is True


async def test_brave_and_exa_search_shape_requests_and_results(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        _json_response(
            {
                "web": {
                    "results": [
                        {"title": "A", "url": "https://a.test", "description": "aa"},
                        {"title": "B", "url": "https://b.test", "description": "bb"},
                    ]
                }
            }
        ),
        _json_response(
            {
                "results": [
                    {"title": "E", "url": "https://e.test", "highlights": ["one", "two"]}
                ]
            }
        ),
    ]
    calls = _patch_client(monkeypatch, lambda *_: responses.pop(0))

    brave = await BraveSearchBackend().search("query", limit=1)
    exa = await ExaSearchBackend().search("query", limit=2)

    assert calls[0][0] == "GET"
    assert calls[0][2]["params"] == {"q": "query", "count": 1}
    assert brave[0].title == "A"
    assert exa[0].snippet == "one two"


async def test_firecrawl_google_parallel_tavily_and_xai_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_URL", "http://firecrawl.local")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    monkeypatch.setenv("GOOGLE_CSE_ID", "cse-id")
    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-key")
    monkeypatch.setenv("PARALLEL_SEARCH_MODE", "fast")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    responses = [
        _json_response(
            {"data": [{"title": "F", "url": "https://f.test", "description": "fire"}]}
        ),
        _json_response(
            {"items": [{"title": "G", "link": "https://g.test", "snippet": "google"}]}
        ),
        _json_response(
            {"results": [{"title": "P", "url": "https://p.test", "excerpts": ["one", "two"]}]}
        ),
        _json_response(
            {"results": [{"title": "T", "url": "https://t.test", "content": "tavily"}]}
        ),
        _json_response({"citations": ["https://x.test"], "choices": []}),
        _json_response({"choices": [{"message": {"content": "grok answer"}}]}),
        _json_response({"choices": [{"message": {"content": ""}}]}),
    ]
    calls = _patch_client(monkeypatch, lambda *_: responses.pop(0))

    firecrawl = await FirecrawlSearchBackend().search("query", limit=1)
    google = await GoogleSearchBackend().search("query", limit=1)
    parallel = await ParallelSearchBackend().search("query", limit=1)
    tavily = await TavilySearchBackend().search("query", limit=1)
    xai_citation = await XaiSearchBackend().search("query", limit=1)
    xai_answer = await XaiSearchBackend().search("query", limit=1)
    xai_empty = await XaiSearchBackend().search("query", limit=1)

    assert calls[0][1] == "http://firecrawl.local/v2/search"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer fc-key"
    assert calls[1][0] == "GET"
    assert calls[1][2]["params"]["key"] == "google-key"
    assert calls[2][2]["json"]["mode"] == "fast"
    assert calls[3][1] == "https://api.tavily.com/search"
    assert calls[3][2]["headers"]["Authorization"] == "Bearer tavily-key"
    assert "api_key" not in calls[3][2]["json"]
    assert calls[4][1] == "https://api.x.ai/v1/chat/completions"
    assert firecrawl[0].snippet == "fire"
    assert google[0].url == "https://g.test"
    assert parallel[0].snippet == "one two"
    assert tavily[0].snippet == "tavily"
    assert xai_citation[0].url == "https://x.test"
    assert xai_answer[0].title == "Grok answer"
    assert xai_empty == []


async def test_kimi_search_prefers_structured_results_and_falls_back_to_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "kimi")
    responses = [
        _json_response(
            {"search_results": [{"title": "K", "url": "https://k.test", "snippet": "kk"}]}
        ),
        _json_response({"choices": [{"message": {"content": "answer text"}}]}),
        _json_response({"choices": [{"message": {"content": ""}}]}),
    ]
    calls = _patch_client(monkeypatch, lambda *_: responses.pop(0))

    structured = await KimiSearchBackend().search("query", limit=3)
    fallback = await KimiSearchBackend().search("query", limit=3)
    empty = await KimiSearchBackend().search("query", limit=3)

    assert calls[0][2]["headers"]["Authorization"] == "Bearer kimi"
    assert structured[0].url == "https://k.test"
    assert fallback[0].title == "Kimi answer"
    assert empty == []


async def test_perplexity_search_uses_direct_or_openrouter_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _json_response({"citations": ["https://one.test", "https://two.test"], "choices": []}),
        _json_response({"choices": [{"message": {"content": "synthesised answer"}}]}),
        _json_response({"choices": [{"message": {"content": ""}}]}),
    ]
    calls = _patch_client(monkeypatch, lambda *_: responses.pop(0))

    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-key")
    citations = await PerplexitySearchBackend().search("query", limit=1)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "openrouter-key")
    fallback = await PerplexitySearchBackend().search("query", limit=3)
    empty = await PerplexitySearchBackend().search("query", limit=3)

    assert calls[0][1] == "https://api.perplexity.ai/chat/completions"
    assert calls[1][1] == "https://openrouter.ai/api/v1/chat/completions"
    assert citations[0].url == "https://one.test"
    assert fallback[0].snippet == "synthesised answer"
    assert empty == []
