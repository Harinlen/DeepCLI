"""McpAuthTool — LLM-callable tool to trigger OAuth for NeedsAuth MCP servers.

Dynamically registered by ToolManager._sync_mcp when a server is in
NeedsAuthServer state.  Replaced by the server's real tools after
successful authentication.

Mirrors Claude Code's ``tools/McpAuthTool/McpAuthTool.ts``:

- ``authenticate`` returns the authorization URL **immediately** so the
  LLM can present it to the user.
- The OAuth flow continues in the **background**: browser callback →
  token exchange → SecretManager persistence → MCPManager reconnect →
  ``on_tools_changed`` signal → ToolManager swaps this pseudo-tool for
  the server's real tools.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, ClassVar

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.mcp_adapter import _normalize
from kernel.tools.tool import Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallResult,
)

if TYPE_CHECKING:
    from kernel.mcp import MCPManager
    from kernel.secrets import SecretManager
    from kernel.tools.context import ToolContext

logger = logging.getLogger(__name__)


class McpAuthTool(Tool[dict[str, Any], dict[str, Any]]):
    """Trigger OAuth authorization for an MCP server.

    The LLM sees this tool when a server requires authentication.
    Calling it starts the OAuth flow and **immediately returns the
    authorization URL** for the LLM to present to the user.

    The OAuth completion (browser callback → token exchange → reconnect)
    happens in the background.  Once it finishes, ``on_tools_changed``
    fires and ToolManager replaces this pseudo-tool with the server's
    real tools.
    """

    kind: ClassVar[ToolKind] = ToolKind.other
    should_defer: ClassVar[bool] = False

    def __init__(
        self,
        server_name: str,
        server_url: str,
        mcp: MCPManager,
        secrets: SecretManager,
    ) -> None:
        self._server_name = server_name
        self._server_url = server_url
        self._mcp = mcp
        self._secrets = secrets

        normalized = _normalize(server_name)
        self.name = f"mcp__{normalized}__authenticate"  # type: ignore[misc]
        self.description = (  # type: ignore[misc]
            f"The {server_name} MCP server ({server_url}) is installed "
            f"but requires authentication. Call this tool to start the "
            f"OAuth flow — you'll receive an authorization URL to share "
            f"with the user. Once the user completes authorization in "
            f"their browser, the server's tools will become available "
            f"automatically."
        )
        self.input_schema: dict[str, Any] = {  # type: ignore[misc]
            "type": "object",
            "properties": {},
        }

    def default_risk(self, input: dict[str, Any], ctx: Any = None) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="high",
            default_decision="ask",
            reason="opens browser for OAuth authorization",
        )

    async def call(
        self, input: dict[str, Any], ctx: ToolContext
    ) -> AsyncGenerator[ToolCallResult, None]:
        from kernel.mcp.oauth import (
            OAuthDiscoveryError,
            OAuthRegistrationError,
            build_authorization_url,
            discover_oauth_metadata,
            generate_pkce,
            register_client,
        )
        from kernel.mcp.oauth_callback import run_callback_server
        from kernel.mcp.types import ConnectedServer, NeedsAuthServer

        import secrets as secrets_module

        name = self._server_name

        # Verify server is actually in NeedsAuth state.
        conn = self._mcp.get_connections().get(name)
        if not isinstance(conn, NeedsAuthServer):
            yield ToolCallResult(
                data={"status": "error", "message": f"Server {name!r} does not need authentication."},
                llm_content=[TextBlock(text=f"Server {name!r} is not in NeedsAuth state.")],
                display=TextDisplay(text=f"Server {name!r} is not in NeedsAuth state."),
            )
            return

        # --- Phase 1: build the auth URL (fast, return to LLM immediately) ---

        try:
            metadata = await discover_oauth_metadata(self._server_url)
        except OAuthDiscoveryError as exc:
            yield ToolCallResult(
                data={"status": "error", "message": str(exc)},
                llm_content=[TextBlock(text=f"OAuth discovery failed for {name}: {exc}")],
                display=TextDisplay(text=f"OAuth discovery failed for {name}: {exc}"),
            )
            return

        # Start callback server to get the port for redirect_uri.
        state = secrets_module.token_urlsafe(16)
        try:
            callback_handle = await run_callback_server(state)
        except OSError as exc:
            yield ToolCallResult(
                data={"status": "error", "message": f"Cannot start callback server: {exc}"},
                llm_content=[TextBlock(text=f"Cannot start OAuth callback server: {exc}")],
                display=TextDisplay(text=f"Cannot start OAuth callback server: {exc}"),
            )
            return

        redirect_uri = f"http://127.0.0.1:{callback_handle.port}/oauth/callback"

        # Client registration (or reuse cached client_id).
        existing = self._secrets.get_oauth_token(name)
        cached_config = existing.client_config if existing else None
        client_id = cached_config.get("client_id") if cached_config else None
        client_secret = cached_config.get("client_secret") if cached_config else None

        if not client_id:
            try:
                client_id, client_secret = await register_client(metadata, redirect_uri)
            except OAuthRegistrationError as exc:
                callback_handle._server.close()
                yield ToolCallResult(
                    data={"status": "error", "message": str(exc)},
                    llm_content=[TextBlock(text=f"OAuth client registration failed for {name}: {exc}")],
                    display=TextDisplay(text=f"OAuth client registration failed: {exc}"),
                )
                return

        # PKCE.
        verifier, challenge = generate_pkce()

        # Build the URL.
        auth_url = build_authorization_url(
            metadata, client_id, redirect_uri, challenge, state,
        )

        # --- Phase 2: background task — wait for callback, exchange, reconnect ---

        async def _complete_oauth() -> None:
            try:
                code = await callback_handle.wait_for_code(timeout=120.0)

                from kernel.mcp.oauth import exchange_code
                from kernel.secrets.types import OAuthToken
                from datetime import datetime, timedelta, timezone

                token_resp = await exchange_code(
                    metadata, code, client_id, client_secret, redirect_uri, verifier
                )

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
                self._secrets.set_oauth_token(name, stored_token)
                logger.info("McpAuthTool: OAuth tokens persisted for %s", name)

                # Reconnect with the new token.
                new_conn = await self._mcp.reconnect_with_token(
                    name, stored_token.access_token
                )
                if isinstance(new_conn, ConnectedServer):
                    await self._mcp.on_tools_changed.emit()
                    logger.info(
                        "McpAuthTool: %s reconnected — real tools now available", name
                    )
                else:
                    logger.warning(
                        "McpAuthTool: OAuth succeeded but reconnect failed for %s", name
                    )

            except TimeoutError:
                logger.warning("McpAuthTool: OAuth callback timed out for %s", name)
            except Exception:
                logger.exception("McpAuthTool: background OAuth failed for %s", name)

        # Fire and forget — the LLM gets the URL immediately.
        asyncio.create_task(_complete_oauth(), name=f"mcp-oauth-{name}")

        # --- Return the URL to the LLM ---

        message = (
            f"Ask the user to open this URL in their browser to authorize "
            f"the {name} MCP server:\n\n{auth_url}\n\n"
            f"Once they complete the flow, the server's tools will become "
            f"available automatically."
        )
        yield ToolCallResult(
            data={"status": "auth_url", "authUrl": auth_url, "message": message},
            llm_content=[TextBlock(text=message)],
            display=TextDisplay(text=f"OAuth URL generated for {name}"),
        )
