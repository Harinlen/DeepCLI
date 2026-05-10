"""Unit tests for fetch_with_fallback — mock backends."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import httpx

import kernel.agents.mustang.tools.web.fetch_backends as fetch_backends
from kernel.agents.mustang.tools.web.fetch_backends import (
    _has_env,
    _looks_like_anti_bot,
    fetch_with_fallback,
    get_available_backends,
)
from kernel.agents.mustang.tools.web.fetch_backends.base import FetchBackend, FetchResult
from kernel.agents.mustang.tools.web.fetch_backends.httpx_html import (
    _HEADERS,
    HttpxFetchBackend,
    _send_with_redirect_check,
)
from kernel.agents.mustang.tools.web.fetch_backends.playwright_be import PlaywrightFetchBackend
from kernel.agents.mustang.tools.web.fetch_backends.readability_be import ReadabilityFetchBackend


# ── Mock backend ──


class MockFetchBackend(FetchBackend):
    def __init__(
        self,
        name: str,
        *,
        content: str = "",
        error: str | None = None,
        status_code: int = 200,
        raise_exc: Exception | None = None,
    ):
        self.name = name
        self._content = content
        self._error = error
        self._status_code = status_code
        self._raise_exc = raise_exc

    def is_available(self) -> bool:
        return True

    async def fetch(self, url: str, *, max_chars: int = 50_000) -> FetchResult:
        if self._raise_exc:
            raise self._raise_exc
        return FetchResult(
            url=url,
            content=self._content[:max_chars],
            content_type="text/html",
            status_code=self._status_code,
            error=self._error,
        )


# ── Tests ──


async def test_fallback_skips_error_backends():
    fail = MockFetchBackend("fail", error="connection refused")
    ok = MockFetchBackend("ok", content="# Real Content\n\n" + "x" * 100)
    result, name = await fetch_with_fallback(
        "https://example.com", backends=[fail, ok]
    )
    assert name == "ok"
    assert "Real Content" in result.content


async def test_fallback_skips_anti_bot():
    antibot = MockFetchBackend("antibot", content="Just a moment please verify" + "x" * 100, status_code=403)
    ok = MockFetchBackend("ok", content="# Good page\n\n" + "x" * 100)
    result, name = await fetch_with_fallback(
        "https://example.com", backends=[antibot, ok]
    )
    assert name == "ok"


async def test_fallback_all_fail():
    fail1 = MockFetchBackend("a", raise_exc=RuntimeError("timeout"))
    fail2 = MockFetchBackend("b", raise_exc=RuntimeError("403"))
    result, name = await fetch_with_fallback(
        "https://example.com", backends=[fail1, fail2]
    )
    assert result.error
    assert "All backends failed" in (result.error or name)


async def test_fetch_with_fallback_caches_success(monkeypatch):
    fetch_backends._FETCH_CACHE.clear()
    backend = MockFetchBackend("ok", content="cached page" + "x" * 100)
    original_fetch = backend.fetch
    calls = 0

    async def _fetch(url: str, *, max_chars: int = 50_000) -> FetchResult:
        nonlocal calls
        calls += 1
        return await original_fetch(url, max_chars=max_chars)

    monkeypatch.setattr(fetch_backends, "get_available_backends", lambda: [backend])
    monkeypatch.setattr(backend, "fetch", _fetch)

    first, first_name = await fetch_with_fallback("https://cache.test")
    second, second_name = await fetch_with_fallback("https://cache.test")

    assert first_name == second_name == "ok"
    assert first.cached is False
    assert second.cached is True
    assert second.content == first.content
    assert calls == 1


async def test_preferred_tried_first():
    slow = MockFetchBackend("slow", content="slow" + "x" * 100)
    fast = MockFetchBackend("fast", content="fast" + "x" * 100)
    result, name = await fetch_with_fallback(
        "https://example.com", backends=[slow, fast], preferred="fast"
    )
    assert name == "fast"


async def test_httpx_result_preserved_on_total_failure():
    """When all fail, httpx result is returned if available."""
    httpx_be = MockFetchBackend("httpx", content="partial", status_code=403)
    # anti-bot detection will trigger on status 403 + short content
    other = MockFetchBackend("other", raise_exc=RuntimeError("down"))
    result, name = await fetch_with_fallback(
        "https://example.com", backends=[other, httpx_be]
    )
    # httpx_result should be preserved as fallback
    assert "httpx" in name or result.error is not None


# ── Anti-bot detection ──


def test_anti_bot_empty_content():
    assert _looks_like_anti_bot(
        FetchResult(url="", content="", content_type="text/html", status_code=200)
    )


def test_anti_bot_very_short_content():
    assert _looks_like_anti_bot(
        FetchResult(url="", content="hi", content_type="text/html", status_code=200)
    )


def test_anti_bot_captcha_403():
    assert _looks_like_anti_bot(
        FetchResult(
            url="",
            content="Please complete the captcha to continue" + "x" * 300,
            content_type="text/html",
            status_code=403,
        )
    )


def test_anti_bot_cloudflare():
    assert _looks_like_anti_bot(
        FetchResult(
            url="",
            content="Checking if the site connection is secure. Cloudflare" + "x" * 300,
            content_type="text/html",
            status_code=503,
        )
    )


def test_not_anti_bot_normal_page():
    assert not _looks_like_anti_bot(
        FetchResult(
            url="",
            content="x" * 100,
            content_type="text/html",
            status_code=200,
        )
    )


def test_not_anti_bot_short_but_legitimate():
    """Short pages (e.g. example.com ~170 chars) must NOT be flagged."""
    assert not _looks_like_anti_bot(
        FetchResult(
            url="",
            content="# Example Domain\n\nThis is a real page." + "x" * 30,
            content_type="text/html",
            status_code=200,
        )
    )


def test_fetch_has_env_trims_empty_values(monkeypatch):
    monkeypatch.delenv("DEEPCLI_TEST_KEY", raising=False)
    assert _has_env("DEEPCLI_TEST_KEY") is False
    monkeypatch.setenv("DEEPCLI_TEST_KEY", "   ")
    assert _has_env("DEEPCLI_TEST_KEY") is False
    monkeypatch.setenv("DEEPCLI_TEST_KEY", " value ")
    assert _has_env("DEEPCLI_TEST_KEY") is True


def test_get_available_fetch_backends_always_includes_httpx(monkeypatch):
    for key in ("FIRECRAWL_API_KEY", "FIRECRAWL_API_URL", "EXA_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    names = [backend.name for backend in get_available_backends()]

    assert names[-1] == "httpx"
    assert "httpx" in names


async def test_httpx_fetch_blocks_private_domain_before_network():
    result = await HttpxFetchBackend().fetch("http://127.0.0.1/private")

    assert result.content == ""
    assert result.error
    assert "rejected" in result.error.lower()


async def test_httpx_fetch_uses_browser_headers_and_reports_http_errors(monkeypatch):
    captured_headers: dict[str, str] = {}

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            captured_headers.update(kwargs["headers"])

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, method: str, url: str) -> httpx.Response:
            assert method == "GET"
            return httpx.Response(
                403,
                text="<html><body>blocked</body></html>",
                headers={"content-type": "text/html"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

    result = await HttpxFetchBackend().fetch("https://example.test")

    assert "Mozilla/5.0" in captured_headers["User-Agent"]
    assert "text/markdown" in captured_headers["Accept"]
    assert result.status_code == 403
    assert result.error == "HTTP 403: Forbidden"
    assert "blocked" in result.content


async def test_httpx_fetch_user_agent_can_be_overridden(monkeypatch):
    captured_headers: dict[str, str] = {}

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            captured_headers.update(kwargs["headers"])

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, method: str, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                text="ok",
                headers={"content-type": "text/plain"},
                request=httpx.Request(method, url),
            )

    monkeypatch.setenv("DEEPCLI_WEB_FETCH_USER_AGENT", "CustomFetch/1.0")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

    result = await HttpxFetchBackend().fetch("https://example.test")

    assert result.error is None
    assert captured_headers["User-Agent"] == "CustomFetch/1.0"


async def test_httpx_fetch_converts_html_without_content_type(monkeypatch):
    class _Client:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, method: str, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                text="<html><body><h1>Hello</h1><p>World</p></body></html>",
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

    result = await HttpxFetchBackend().fetch("https://example.test")

    assert result.error is None
    assert "Hello" in result.content
    assert "World" in result.content


async def test_httpx_fetch_pretty_prints_json(monkeypatch):
    class _Client:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, method: str, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                text='{"b":2,"a":{"nested":true}}',
                headers={"content-type": "application/json"},
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

    result = await HttpxFetchBackend().fetch("https://example.test/data")

    assert result.error is None
    assert '"nested": true' in result.content
    assert "\n" in result.content


async def test_httpx_fetch_rejects_binary_content(monkeypatch):
    class _Client:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, method: str, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"%PDF-1.7\x00binary",
                headers={"content-type": "application/pdf"},
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

    result = await HttpxFetchBackend().fetch("https://example.test/file.pdf")

    assert result.error is not None
    assert "Unsupported binary" in result.error
    assert result.raw_length > 0


async def test_send_with_redirect_check_blocks_bad_redirect():
    request = httpx.Request("GET", "https://safe.test")
    redirect = httpx.Response(
        302,
        headers={"location": "http://127.0.0.1/private"},
        request=request,
    )

    class _Client:
        async def request(self, method: str, url: str) -> httpx.Response:
            assert method == "GET"
            assert url == "https://safe.test"
            return redirect

    try:
        await _send_with_redirect_check(_Client(), "https://safe.test")
    except httpx.HTTPStatusError as exc:
        assert "Redirect blocked" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("redirect should have been blocked")


async def test_readability_fetch_success_and_http_error(monkeypatch):
    class _Document:
        def __init__(self, html: str) -> None:
            self.html = html

        def summary(self) -> str:
            return "<main><h1>Hello</h1><p>World</p></main>"

        def title(self) -> str:
            return "Readable"

    class _Client:
        def __init__(self, *, fail: bool = False, **_: Any) -> None:
            self.fail = fail

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def request(self, method: str, url: str) -> httpx.Response:
            assert method == "GET"
            if self.fail:
                raise httpx.ConnectError("offline", request=httpx.Request("GET", url))
            return httpx.Response(
                200,
                text="<html><body>Hello</body></html>",
                headers={"content-type": "text/html"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setitem(sys.modules, "readability", SimpleNamespace(Document=_Document))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))

    result = await ReadabilityFetchBackend().fetch("https://example.test", max_chars=20)
    assert _HEADERS["User-Agent"].startswith("Mozilla/5.0")
    assert result.title == "Readable"
    assert "Hello" in result.content

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(fail=True, **kwargs))
    failed = await ReadabilityFetchBackend().fetch("https://example.test")
    assert failed.error == "HTTP error: offline"


async def test_playwright_fetch_blocks_domain_before_optional_import():
    result = await PlaywrightFetchBackend().fetch("http://127.0.0.1/private")

    assert result.error
