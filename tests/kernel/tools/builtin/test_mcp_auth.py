from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kernel.mcp.oauth import OAuthDiscoveryError, OAuthMetadata, OAuthRegistrationError
from kernel.mcp.types import ConnectedServer, NeedsAuthServer
from kernel.secrets.types import OAuthToken
from kernel.tools.builtin.mcp_auth import McpAuthTool
from kernel.tools.context import ToolContext


class _CallbackHandle:
    port = 8123

    def __init__(self) -> None:
        self._server = MagicMock()

    async def wait_for_code(self, timeout: float) -> str:
        await asyncio.sleep(timeout)
        return "never"


def _ctx() -> ToolContext:
    return ToolContext(
        session_id="s-1",
        agent_depth=0,
        agent_id=None,
        cwd=Path("/tmp"),
        cancel_event=asyncio.Event(),
        file_state=MagicMock(),
    )


async def _collect(tool: McpAuthTool) -> list[Any]:
    return [event async for event in tool.call({}, _ctx())]


def _tool(
    *,
    connection: object,
    token: OAuthToken | None = None,
) -> tuple[McpAuthTool, MagicMock, MagicMock]:
    mcp = MagicMock()
    mcp.get_connections.return_value = {"alpha": connection}
    secrets = MagicMock()
    secrets.get_oauth_token.return_value = token
    return McpAuthTool("alpha", "https://mcp.example", mcp, secrets), mcp, secrets


@pytest.mark.anyio
async def test_mcp_auth_tool_reports_server_that_no_longer_needs_auth() -> None:
    tool, _mcp, _secrets = _tool(connection=ConnectedServer("alpha", MagicMock()))

    [result] = await _collect(tool)

    assert result.data == {
        "status": "error",
        "message": "Server 'alpha' does not need authentication.",
    }
    assert "not in NeedsAuth state" in result.display.text


@pytest.mark.anyio
async def test_mcp_auth_tool_reports_discovery_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    tool, _mcp, _secrets = _tool(connection=NeedsAuthServer("alpha"))

    async def _discover(server_url: str) -> OAuthMetadata:
        assert server_url == "https://mcp.example"
        raise OAuthDiscoveryError("metadata unavailable")

    monkeypatch.setattr("kernel.mcp.oauth.discover_oauth_metadata", _discover)

    [result] = await _collect(tool)

    assert result.data == {"status": "error", "message": "metadata unavailable"}
    assert "OAuth discovery failed" in result.llm_content[0].text


@pytest.mark.anyio
async def test_mcp_auth_tool_reports_callback_server_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, _mcp, _secrets = _tool(connection=NeedsAuthServer("alpha"))

    async def _discover(_: str) -> OAuthMetadata:
        return OAuthMetadata(
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            registration_endpoint="https://auth.example/register",
        )

    async def _callback(_: str) -> _CallbackHandle:
        raise OSError("port busy")

    monkeypatch.setattr("kernel.mcp.oauth.discover_oauth_metadata", _discover)
    monkeypatch.setattr("kernel.mcp.oauth_callback.run_callback_server", _callback)

    [result] = await _collect(tool)

    assert result.data == {
        "status": "error",
        "message": "Cannot start callback server: port busy",
    }
    assert "Cannot start OAuth callback server" in result.llm_content[0].text


@pytest.mark.anyio
async def test_mcp_auth_tool_closes_callback_on_registration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _CallbackHandle()
    tool, _mcp, _secrets = _tool(connection=NeedsAuthServer("alpha"))

    async def _discover(_: str) -> OAuthMetadata:
        return OAuthMetadata(
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            registration_endpoint=None,
        )

    async def _callback(_: str) -> _CallbackHandle:
        return handle

    async def _register(_: OAuthMetadata, __: str) -> tuple[str, str | None]:
        raise OAuthRegistrationError("registration disabled")

    monkeypatch.setattr("kernel.mcp.oauth.discover_oauth_metadata", _discover)
    monkeypatch.setattr("kernel.mcp.oauth_callback.run_callback_server", _callback)
    monkeypatch.setattr("kernel.mcp.oauth.register_client", _register)

    [result] = await _collect(tool)

    handle._server.close.assert_called_once_with()
    assert result.data == {"status": "error", "message": "registration disabled"}
    assert "OAuth client registration failed" in result.llm_content[0].text


@pytest.mark.anyio
async def test_mcp_auth_tool_returns_url_with_cached_client_and_starts_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    token = OAuthToken(
        access_token="old-token",
        client_config={"client_id": "cached-client", "client_secret": "cached-secret"},
    )
    tool, _mcp, secrets = _tool(connection=NeedsAuthServer("alpha"), token=token)

    async def _discover(_: str) -> OAuthMetadata:
        return OAuthMetadata(
            authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token",
            registration_endpoint="https://auth.example/register",
        )

    async def _callback(_: str) -> _CallbackHandle:
        return _CallbackHandle()

    def _create_task(coro: Any, *, name: str) -> MagicMock:
        created.append({"coro": coro, "name": name})
        coro.close()
        return MagicMock()

    monkeypatch.setattr("kernel.mcp.oauth.discover_oauth_metadata", _discover)
    monkeypatch.setattr("kernel.mcp.oauth_callback.run_callback_server", _callback)
    monkeypatch.setattr("kernel.mcp.oauth.generate_pkce", lambda: ("verifier", "challenge"))
    monkeypatch.setattr("kernel.tools.builtin.mcp_auth.asyncio.create_task", _create_task)

    [result] = await _collect(tool)

    secrets.get_oauth_token.assert_called_once_with("alpha")
    assert result.data["status"] == "auth_url"
    assert "client_id=cached-client" in result.data["authUrl"]
    assert "code_challenge=challenge" in result.data["authUrl"]
    assert created[0]["name"] == "mcp-oauth-alpha"
    assert result.display.text == "OAuth URL generated for alpha"
