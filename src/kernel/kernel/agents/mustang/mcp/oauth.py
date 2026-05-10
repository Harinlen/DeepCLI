"""MCP OAuth 2.0 Authorization Code flow with PKCE.

Implements RFC 9728 (Protected Resource Metadata) / RFC 8414
(Authorization Server Metadata) discovery, RFC 7636 (PKCE), and
RFC 7591 (Dynamic Client Registration).

Dependencies: httpx (already in kernel deps), stdlib only otherwise.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets as secrets_module
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlparse

import httpx

if TYPE_CHECKING:
    from kernel.core.secrets import SecretManager
    from kernel.core.secrets.types import OAuthToken as StoredOAuthToken

logger = logging.getLogger(__name__)

_DISCOVERY_TIMEOUT = 10.0
_TOKEN_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthMetadata:
    """Discovered OAuth authorization server metadata."""

    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    scopes_supported: list[str] = field(default_factory=list)
    # Raw metadata for caching in client_config.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class TokenResponse:
    """Raw token endpoint response."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: str | None = None


# ---------------------------------------------------------------------------
# PKCE (RFC 7636)
# ---------------------------------------------------------------------------


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for S256 PKCE.

    The verifier is 43 chars of base64url randomness (32 random bytes).
    The challenge is ``base64url(SHA256(verifier))``.
    """
    verifier = secrets_module.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Metadata discovery
# ---------------------------------------------------------------------------


async def discover_oauth_metadata(server_url: str) -> OAuthMetadata:
    """Discover the OAuth authorization server for an MCP server.

    Tries RFC 9728 (``/.well-known/oauth-protected-resource``) first,
    then falls back to RFC 8414 on the server's origin.

    Raises :class:`OAuthDiscoveryError` if discovery fails completely.
    """
    parsed = urlparse(server_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT) as client:
        # --- RFC 9728: Protected Resource Metadata ---
        try:
            resource_url = f"{origin}/.well-known/oauth-protected-resource"
            resp = await client.get(resource_url)
            if resp.status_code == 200:
                resource = resp.json()
                auth_servers = resource.get("authorization_servers", [])
                if auth_servers:
                    as_url = auth_servers[0]
                    metadata = await _fetch_as_metadata(client, as_url)
                    if metadata:
                        return metadata
        except (httpx.HTTPError, KeyError, ValueError):
            logger.debug("RFC 9728 discovery failed for %s", server_url)

        # --- RFC 8414 fallback: origin-level ---
        try:
            metadata = await _fetch_as_metadata(client, origin)
            if metadata:
                return metadata
        except (httpx.HTTPError, KeyError, ValueError):
            pass

        raise OAuthDiscoveryError(
            f"Could not discover OAuth metadata for {server_url}. "
            f"Server does not expose RFC 9728 or RFC 8414 endpoints."
        )


async def _fetch_as_metadata(
    client: httpx.AsyncClient, as_url: str
) -> OAuthMetadata | None:
    """Fetch RFC 8414 Authorization Server Metadata."""
    url = f"{as_url.rstrip('/')}/.well-known/oauth-authorization-server"
    resp = await client.get(url)
    if resp.status_code != 200:
        return None
    data = resp.json()
    auth_ep = data.get("authorization_endpoint")
    token_ep = data.get("token_endpoint")
    if not auth_ep or not token_ep:
        return None
    return OAuthMetadata(
        authorization_endpoint=auth_ep,
        token_endpoint=token_ep,
        registration_endpoint=data.get("registration_endpoint"),
        scopes_supported=data.get("scopes_supported", []),
        raw=data,
    )


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------


async def register_client(
    metadata: OAuthMetadata,
    redirect_uri: str,
    client_name: str = "DeepCLI",
) -> tuple[str, str | None]:
    """Dynamically register an OAuth client.

    Returns ``(client_id, client_secret | None)``.
    Raises :class:`OAuthRegistrationError` on failure.
    """
    if not metadata.registration_endpoint:
        raise OAuthRegistrationError(
            "Server does not support dynamic client registration. "
            "Set 'oauth_client_id' in the MCP server config."
        )
    payload = {
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "client_name": client_name,
    }
    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT) as client:
        resp = await client.post(metadata.registration_endpoint, json=payload)
        if resp.status_code not in (200, 201):
            raise OAuthRegistrationError(
                f"Client registration failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        return data["client_id"], data.get("client_secret")


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------


def build_authorization_url(
    metadata: OAuthMetadata,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scopes: list[str] | None = None,
) -> str:
    """Build the authorization URL the user opens in their browser."""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    return f"{metadata.authorization_endpoint}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Token exchange & refresh
# ---------------------------------------------------------------------------


async def exchange_code(
    metadata: OAuthMetadata,
    code: str,
    client_id: str,
    client_secret: str | None,
    redirect_uri: str,
    code_verifier: str,
) -> TokenResponse:
    """Exchange an authorization code for tokens (RFC 6749 §4.1.3)."""
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    return await _post_token_endpoint(metadata.token_endpoint, payload)


async def refresh_access_token(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str | None = None,
) -> TokenResponse:
    """Refresh an access token using a refresh token."""
    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    return await _post_token_endpoint(token_endpoint, payload)


async def _post_token_endpoint(url: str, payload: dict[str, str]) -> TokenResponse:
    """POST to a token endpoint and parse the response."""
    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT) as client:
        resp = await client.post(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise OAuthTokenError(
                f"Token endpoint returned {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        return TokenResponse(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in"),
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
        )


# ---------------------------------------------------------------------------
# Top-level flow orchestrator
# ---------------------------------------------------------------------------


async def perform_oauth_flow(
    server_key: str,
    server_url: str,
    secrets: SecretManager,
    *,
    scopes: list[str] | None = None,
    cached_client_config: dict[str, Any] | None = None,
) -> StoredOAuthToken:
    """Run the full OAuth 2.0 Authorization Code + PKCE flow.

    1. Discover OAuth metadata
    2. Register client (or reuse cached client_id)
    3. Generate PKCE + state
    4. Start local callback server
    5. Open browser for authorization
    6. Wait for callback with auth code
    7. Exchange code for tokens
    8. Persist via SecretManager
    9. Return the stored token

    Args:
        server_key: MCP server name (used as SecretManager key).
        server_url: MCP server URL for OAuth discovery.
        secrets: SecretManager instance for token persistence.
        scopes: Optional list of requested scopes.
        cached_client_config: Reuse client_id/secret from a previous flow.

    Returns:
        The persisted :class:`OAuthToken`.
    """
    from kernel.agents.mustang.mcp.oauth_callback import run_callback_server
    from kernel.core.secrets.types import OAuthToken

    # 1. Discover metadata.
    metadata = await discover_oauth_metadata(server_url)
    logger.info("OAuth metadata discovered for %s: %s", server_key, metadata.token_endpoint)

    # 2. Client registration (or reuse).
    client_id: str | None = None
    client_secret: str | None = None
    if cached_client_config:
        client_id = cached_client_config.get("client_id")
        client_secret = cached_client_config.get("client_secret")

    # 3. Start callback server first to get the port for redirect_uri.
    state = secrets_module.token_urlsafe(16)
    server_handle = await run_callback_server(state)
    redirect_uri = f"http://127.0.0.1:{server_handle.port}/oauth/callback"

    # 4. Register client if no cached client_id.
    if not client_id:
        try:
            client_id, client_secret = await register_client(metadata, redirect_uri)
            logger.info("OAuth client registered: %s", client_id)
        except OAuthRegistrationError:
            raise OAuthFlowError(
                f"Server {server_key!r} requires OAuth but does not support "
                f"dynamic client registration and no client_id is configured."
            )

    # 5. PKCE.
    verifier, challenge = generate_pkce()

    # 6. Open browser.
    auth_url = build_authorization_url(
        metadata, client_id, redirect_uri, challenge, state, scopes
    )
    logger.info("Opening browser for OAuth authorization: %s", server_key)
    webbrowser.open(auth_url)

    # 7. Wait for callback.
    try:
        code = await server_handle.wait_for_code(timeout=120.0)
    except TimeoutError:
        raise OAuthFlowError(
            f"OAuth flow timed out for {server_key!r}. "
            f"The browser authorization was not completed within 120 seconds."
        )

    # 8. Exchange code for tokens.
    token_resp = await exchange_code(
        metadata, code, client_id, client_secret, redirect_uri, verifier
    )

    # 9. Build and persist OAuthToken.
    now = datetime.now(timezone.utc)
    expires_at = (
        now + timedelta(seconds=token_resp.expires_in)
        if token_resp.expires_in
        else None
    )
    stored_token = OAuthToken(
        access_token=token_resp.access_token,
        refresh_token=token_resp.refresh_token,
        expires_at=expires_at,
        client_config={
            "client_id": client_id,
            "client_secret": client_secret,
            "token_endpoint": metadata.token_endpoint,
            "authorization_endpoint": metadata.authorization_endpoint,
            "registration_endpoint": metadata.registration_endpoint,
        },
    )
    secrets.set_oauth_token(server_key, stored_token)
    logger.info("OAuth tokens persisted for %s", server_key)

    return stored_token


# ---------------------------------------------------------------------------
# Token refresh helper (called by MCPManager)
# ---------------------------------------------------------------------------


async def try_refresh_token(
    server_key: str,
    secrets: SecretManager,
) -> StoredOAuthToken | None:
    """Attempt to refresh an OAuth token using the stored refresh_token.

    Returns the new token if refresh succeeds, or ``None`` if:
    - No stored token exists
    - No refresh_token available
    - Refresh request fails (invalid_grant, etc.)

    On failure, the stored token is **deleted** to force a fresh OAuth flow.
    """
    from kernel.core.secrets.types import OAuthToken

    existing = secrets.get_oauth_token(server_key)
    if existing is None or existing.refresh_token is None:
        return None

    client_config = existing.client_config
    token_endpoint = client_config.get("token_endpoint")
    client_id = client_config.get("client_id")
    if not token_endpoint or not client_id:
        logger.warning("Cannot refresh %s: missing token_endpoint or client_id", server_key)
        return None

    try:
        token_resp = await refresh_access_token(
            token_endpoint,
            existing.refresh_token,
            client_id,
            client_config.get("client_secret"),
        )
    except OAuthTokenError as exc:
        logger.warning("Token refresh failed for %s: %s — deleting stored token", server_key, exc)
        secrets.delete_oauth_token(server_key)
        return None

    now = datetime.now(timezone.utc)
    expires_at = (
        now + timedelta(seconds=token_resp.expires_in)
        if token_resp.expires_in
        else None
    )
    new_token = OAuthToken(
        access_token=token_resp.access_token,
        refresh_token=token_resp.refresh_token or existing.refresh_token,
        expires_at=expires_at,
        client_config=client_config,
    )
    secrets.set_oauth_token(server_key, new_token)
    logger.info("Token refreshed for %s", server_key)
    return new_token


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OAuthError(Exception):
    """Base class for OAuth errors."""


class OAuthDiscoveryError(OAuthError):
    """OAuth metadata discovery failed."""


class OAuthRegistrationError(OAuthError):
    """Dynamic client registration failed."""


class OAuthTokenError(OAuthError):
    """Token exchange or refresh failed."""


class OAuthFlowError(OAuthError):
    """Top-level flow failure (timeout, user cancel, etc.)."""
