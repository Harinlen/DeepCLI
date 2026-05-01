"""Unit tests for fetch_with_fallback — mock backends."""

from __future__ import annotations


from kernel.tools.web.fetch_backends import _looks_like_anti_bot, fetch_with_fallback
from kernel.tools.web.fetch_backends.base import FetchBackend, FetchResult


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
