"""Unit tests for MCP OAuth flow module."""

from __future__ import annotations

import base64
import hashlib

import pytest
import httpx

from kernel.mcp.oauth import (
    OAuthDiscoveryError,
    OAuthMetadata,
    OAuthTokenError,
    build_authorization_url,
    discover_oauth_metadata,
    exchange_code,
    generate_pkce,
    refresh_access_token,
)


# ---------------------------------------------------------------------------
# Mock httpx transport
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    """httpx transport that returns canned responses by URL pattern."""

    def __init__(self, routes: dict[str, tuple[int, dict]]) -> None:
        self._routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, (status, body) in self._routes.items():
            if pattern in url:
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": "not found"})


def _patch_httpx(monkeypatch, routes: dict[str, tuple[int, dict]]) -> None:
    """Monkey-patch httpx.AsyncClient to use a mock transport."""
    transport = _MockTransport(routes)
    _original = httpx.AsyncClient

    def _factory(**kw):
        kw.pop("timeout", None)  # remove timeout to avoid conflict
        return _original(transport=transport, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def test_generate_pkce_verifier_length():
    verifier, _ = generate_pkce()
    assert len(verifier) == 43  # 32 bytes → 43 base64url chars


def test_generate_pkce_challenge_is_sha256():
    verifier, challenge = generate_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_generate_pkce_unique():
    v1, _ = generate_pkce()
    v2, _ = generate_pkce()
    assert v1 != v2


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------


def test_build_authorization_url_contains_params():
    meta = OAuthMetadata(
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
    )
    url = build_authorization_url(
        meta, "client-123", "http://127.0.0.1:19800/oauth/callback",
        "challenge_value", "state_value", ["read", "write"],
    )
    assert "response_type=code" in url
    assert "client_id=client-123" in url
    assert "code_challenge=challenge_value" in url
    assert "code_challenge_method=S256" in url
    assert "state=state_value" in url
    assert "scope=read+write" in url
    assert url.startswith("https://auth.example.com/authorize?")


def test_build_authorization_url_no_scopes():
    meta = OAuthMetadata(
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
    )
    url = build_authorization_url(
        meta, "cid", "http://localhost/callback", "ch", "st",
    )
    assert "scope=" not in url


# ---------------------------------------------------------------------------
# Metadata discovery
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_discover_metadata_rfc9728(monkeypatch):
    """RFC 9728 path: resource metadata → AS metadata."""
    _patch_httpx(monkeypatch, {
        "/.well-known/oauth-protected-resource": (200, {
            "resource": "https://mcp.example.com",
            "authorization_servers": ["https://auth.example.com"],
        }),
        "auth.example.com/.well-known/oauth-authorization-server": (200, {
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "registration_endpoint": "https://auth.example.com/register",
        }),
    })

    meta = await discover_oauth_metadata("https://mcp.example.com/v1")
    assert meta.authorization_endpoint == "https://auth.example.com/authorize"
    assert meta.token_endpoint == "https://auth.example.com/token"
    assert meta.registration_endpoint == "https://auth.example.com/register"


@pytest.mark.anyio
async def test_discover_metadata_rfc8414_fallback(monkeypatch):
    """RFC 9728 fails → fall back to RFC 8414 on origin."""
    _patch_httpx(monkeypatch, {
        "/.well-known/oauth-protected-resource": (404, {}),
        "mcp.example.com/.well-known/oauth-authorization-server": (200, {
            "authorization_endpoint": "https://mcp.example.com/authorize",
            "token_endpoint": "https://mcp.example.com/token",
        }),
    })

    meta = await discover_oauth_metadata("https://mcp.example.com/api")
    assert meta.token_endpoint == "https://mcp.example.com/token"


@pytest.mark.anyio
async def test_discover_metadata_fails(monkeypatch):
    """Both discovery paths fail → OAuthDiscoveryError."""
    _patch_httpx(monkeypatch, {
        "/.well-known/oauth-protected-resource": (404, {}),
        "/.well-known/oauth-authorization-server": (404, {}),
    })

    with pytest.raises(OAuthDiscoveryError):
        await discover_oauth_metadata("https://mcp.example.com")


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_exchange_code_success(monkeypatch):
    _patch_httpx(monkeypatch, {
        "/token": (200, {
            "access_token": "at_123",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "rt_456",
        }),
    })

    meta = OAuthMetadata(
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
    )
    resp = await exchange_code(meta, "code123", "cid", None, "http://localhost/cb", "verifier")
    assert resp.access_token == "at_123"
    assert resp.refresh_token == "rt_456"
    assert resp.expires_in == 3600


@pytest.mark.anyio
async def test_exchange_code_error(monkeypatch):
    _patch_httpx(monkeypatch, {
        "/token": (400, {"error": "invalid_grant"}),
    })

    meta = OAuthMetadata(
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
    )
    with pytest.raises(OAuthTokenError):
        await exchange_code(meta, "bad", "cid", None, "http://localhost/cb", "v")


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_token_success(monkeypatch):
    _patch_httpx(monkeypatch, {
        "/token": (200, {
            "access_token": "new_at",
            "expires_in": 7200,
        }),
    })

    resp = await refresh_access_token(
        "https://auth.example.com/token", "rt_old", "cid",
    )
    assert resp.access_token == "new_at"
    assert resp.expires_in == 7200
