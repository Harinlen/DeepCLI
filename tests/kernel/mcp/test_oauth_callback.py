"""Unit tests for the OAuth callback server."""

from __future__ import annotations

import asyncio

import pytest

from kernel.mcp.oauth_callback import OAuthCallbackError, run_callback_server


@pytest.mark.anyio
async def test_callback_captures_code():
    """Server captures auth code from a valid callback."""
    handle = await run_callback_server("test-state-123")
    assert handle.port >= 19750

    # Simulate browser callback.
    reader, writer = await asyncio.open_connection("127.0.0.1", handle.port)
    writer.write(
        b"GET /oauth/callback?code=AUTH_CODE_XYZ&state=test-state-123 HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n\r\n"
    )
    await writer.drain()

    # Read response.
    response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
    writer.close()

    assert b"200 OK" in response
    assert b"Authorization successful" in response

    # Get the code.
    code = await asyncio.wait_for(handle._future, timeout=5.0)
    assert code == "AUTH_CODE_XYZ"

    handle._server.close()
    await handle._server.wait_closed()


@pytest.mark.anyio
async def test_callback_state_mismatch():
    """Wrong state parameter raises OAuthCallbackError."""
    handle = await run_callback_server("expected-state")

    reader, writer = await asyncio.open_connection("127.0.0.1", handle.port)
    writer.write(
        b"GET /oauth/callback?code=CODE&state=wrong-state HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n\r\n"
    )
    await writer.drain()

    response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
    writer.close()

    assert b"400 Bad Request" in response
    assert b"State mismatch" in response

    with pytest.raises(OAuthCallbackError):
        await asyncio.wait_for(handle._future, timeout=2.0)

    handle._server.close()
    await handle._server.wait_closed()


@pytest.mark.anyio
async def test_callback_timeout():
    """No callback → TimeoutError."""
    handle = await run_callback_server("state")

    with pytest.raises(TimeoutError):
        await handle.wait_for_code(timeout=0.5)


@pytest.mark.anyio
async def test_callback_oauth_error():
    """Server forwards OAuth error from provider."""
    handle = await run_callback_server("state")

    reader, writer = await asyncio.open_connection("127.0.0.1", handle.port)
    writer.write(
        b"GET /oauth/callback?error=access_denied&error_description=User+denied HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n\r\n"
    )
    await writer.drain()

    response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
    writer.close()

    assert b"400 Bad Request" in response

    with pytest.raises(OAuthCallbackError, match="denied"):
        await asyncio.wait_for(handle._future, timeout=2.0)

    handle._server.close()
    await handle._server.wait_closed()
