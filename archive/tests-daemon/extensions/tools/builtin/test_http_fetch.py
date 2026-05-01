"""Tests for HttpFetchTool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from daemon.extensions.tools.base import PermissionLevel, ToolContext
from daemon.extensions.tools.builtin.http_fetch import HttpFetchTool


@pytest.fixture
def tool() -> HttpFetchTool:
    return HttpFetchTool()


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


def _make_response(
    body: str = "",
    *,
    status_code: int = 200,
    reason_phrase: str = "OK",
    content_type: str = "application/json",
    extra_headers: dict[str, str] | None = None,
    url: str = "https://api.example.com/v1/users",
    is_redirect: bool = False,
) -> MagicMock:
    """Build a mock httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.reason_phrase = reason_phrase
    resp.content = body.encode()
    headers = {"content-type": content_type, "content-length": str(len(body))}
    if extra_headers:
        headers.update(extra_headers)
    # Use real httpx.Headers so .items() iteration works as expected.
    resp.headers = httpx.Headers(headers)
    # Use real httpx.URL so .join() works for relative redirects.
    resp.url = httpx.URL(url)
    resp.is_redirect = is_redirect
    resp.request = MagicMock()
    return resp


def _patch_client(response: MagicMock):
    """Return a patch context that makes httpx.AsyncClient.request return *response*."""
    patcher = patch(
        "daemon.extensions.tools.builtin.http_fetch.httpx.AsyncClient"
    )
    mock_client_cls = patcher.start()
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = mock_client
    return patcher, mock_client


# ── Permission / URL validation ──────────────────────────────


class TestValidation:
    def test_permission_level(self, tool: HttpFetchTool) -> None:
        assert tool.permission_level == PermissionLevel.PROMPT

    @pytest.mark.asyncio
    async def test_rejects_non_http(self, tool: HttpFetchTool, ctx: ToolContext) -> None:
        result = await tool.execute({"url": "file:///etc/passwd"}, ctx)
        assert result.is_error is True
        assert "http(s)" in result.output

    @pytest.mark.asyncio
    async def test_rejects_missing_host(self, tool: HttpFetchTool, ctx: ToolContext) -> None:
        result = await tool.execute({"url": "https://"}, ctx)
        assert result.is_error is True
        assert "host" in result.output.lower()

    @pytest.mark.asyncio
    async def test_rejects_localhost(self, tool: HttpFetchTool, ctx: ToolContext) -> None:
        result = await tool.execute({"url": "http://localhost:8080/api"}, ctx)
        assert result.is_error is True
        assert "localhost" in result.output

    @pytest.mark.asyncio
    async def test_rejects_private_ip(self, tool: HttpFetchTool, ctx: ToolContext) -> None:
        result = await tool.execute({"url": "http://169.254.169.254/meta"}, ctx)
        assert result.is_error is True
        assert "link-local" in result.output

    @pytest.mark.asyncio
    async def test_invalid_method_rejected(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        # Pydantic Literal validation rejects unknown methods.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            HttpFetchTool.Input.model_validate(
                {"url": "https://example.com", "method": "TRACE"}
            )


# ── Successful requests ──────────────────────────────────────


class TestRequests:
    @pytest.mark.asyncio
    async def test_default_method_is_get(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        resp = _make_response('{"ok": true}', content_type="application/json")
        patcher, mock_client = _patch_client(resp)
        try:
            await tool.execute({"url": "https://api.example.com/v1/users"}, ctx)
        finally:
            patcher.stop()
        # First positional arg of request() is the method.
        called_method = mock_client.request.call_args.args[0]
        assert called_method == "GET"

    @pytest.mark.asyncio
    async def test_fetches_and_returns_raw_body(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        body = '{"users": [{"id": 1, "name": "Alice"}]}'
        resp = _make_response(body, content_type="application/json")
        patcher, _ = _patch_client(resp)
        try:
            result = await tool.execute({"url": "https://api.example.com/v1/users"}, ctx)
        finally:
            patcher.stop()
        assert result.is_error is False
        # Body returned verbatim, not parsed/extracted.
        assert body in result.output

    @pytest.mark.asyncio
    async def test_response_format_includes_status_line(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        resp = _make_response('{"ok": true}', status_code=200, reason_phrase="OK")
        patcher, _ = _patch_client(resp)
        try:
            result = await tool.execute({"url": "https://api.example.com/v1/users"}, ctx)
        finally:
            patcher.stop()
        assert result.output.startswith("HTTP/1.1 200 OK")

    @pytest.mark.asyncio
    async def test_post_with_json_body(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        resp = _make_response('{"created": true}', status_code=201, reason_phrase="Created")
        patcher, mock_client = _patch_client(resp)
        try:
            result = await tool.execute(
                {
                    "url": "https://api.example.com/v1/users",
                    "method": "POST",
                    "headers": {"Content-Type": "application/json"},
                    "body": '{"name": "Bob"}',
                },
                ctx,
            )
        finally:
            patcher.stop()
        # Verify call arguments — request was POST with body
        call = mock_client.request.call_args
        assert call.args[0] == "POST"
        assert call.kwargs.get("content") == '{"name": "Bob"}'
        assert result.is_error is False
        assert "201" in result.output

    @pytest.mark.asyncio
    async def test_custom_headers_passthrough(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        resp = _make_response("ok")
        with patch(
            "daemon.extensions.tools.builtin.http_fetch.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await tool.execute(
                {
                    "url": "https://api.example.com/v1/users",
                    "headers": {"Authorization": "Bearer secret"},
                },
                ctx,
            )

            # Headers passed when constructing the AsyncClient
            init_call = mock_client_cls.call_args
            passed_headers = init_call.kwargs["headers"]
            assert passed_headers["Authorization"] == "Bearer secret"


# ── Error cases ───────────────────────────────────────────────


class TestErrorCases:
    @pytest.mark.asyncio
    async def test_4xx_returns_body_with_error(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        resp = _make_response('{"error":"not found"}', status_code=404, reason_phrase="Not Found")
        patcher, _ = _patch_client(resp)
        try:
            result = await tool.execute({"url": "https://api.example.com/v1/users"}, ctx)
        finally:
            patcher.stop()
        assert result.is_error is True
        assert "404" in result.output
        # Body should still be returned so the LLM can see the error payload.
        assert "not found" in result.output

    @pytest.mark.asyncio
    async def test_5xx_returns_body_with_error(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        resp = _make_response("internal server error", status_code=500, reason_phrase="Internal Server Error")
        patcher, _ = _patch_client(resp)
        try:
            result = await tool.execute({"url": "https://api.example.com/v1/users"}, ctx)
        finally:
            patcher.stop()
        assert result.is_error is True
        assert "500" in result.output

    @pytest.mark.asyncio
    async def test_timeout_error_is_actionable(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        with patch(
            "daemon.extensions.tools.builtin.http_fetch.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tool.execute({"url": "https://api.example.com/v1/users"}, ctx)
        assert result.is_error is True
        assert "timeout" in result.output.lower()

    @pytest.mark.asyncio
    async def test_max_chars_truncation(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        body = "x" * 2000
        resp = _make_response(body)
        patcher, _ = _patch_client(resp)
        try:
            result = await tool.execute(
                {"url": "https://api.example.com/v1/users", "max_chars": 100}, ctx
            )
        finally:
            patcher.stop()
        assert result.is_error is False
        assert "truncated" in result.output

    @pytest.mark.asyncio
    async def test_rejects_redirect_to_private(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        # Server redirects to a private IP — should be blocked per-hop.
        redirect_resp = _make_response(
            "",
            status_code=302,
            reason_phrase="Found",
            extra_headers={"location": "http://192.168.1.1/admin"},
            url="https://example.com/redirect",
            is_redirect=True,
        )
        patcher, _ = _patch_client(redirect_resp)
        try:
            result = await tool.execute(
                {"url": "https://example.com/redirect"}, ctx
            )
        finally:
            patcher.stop()
        assert result.is_error is True
        assert "blocked" in result.output.lower()


# ── Header filtering ──────────────────────────────────────────


class TestHeaderFiltering:
    @pytest.mark.asyncio
    async def test_skips_set_cookie_header(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        resp = _make_response(
            '{"ok": true}',
            extra_headers={"set-cookie": "session=secret123; HttpOnly"},
        )
        patcher, _ = _patch_client(resp)
        try:
            result = await tool.execute({"url": "https://api.example.com/v1/users"}, ctx)
        finally:
            patcher.stop()
        assert "Set-Cookie" not in result.output
        assert "set-cookie" not in result.output
        assert "session=secret123" not in result.output

    @pytest.mark.asyncio
    async def test_includes_x_headers(
        self, tool: HttpFetchTool, ctx: ToolContext
    ) -> None:
        resp = _make_response(
            "{}",
            extra_headers={"x-rate-limit-remaining": "42"},
        )
        patcher, _ = _patch_client(resp)
        try:
            result = await tool.execute({"url": "https://api.example.com/v1/users"}, ctx)
        finally:
            patcher.stop()
        assert "rate-limit-remaining" in result.output.lower()
