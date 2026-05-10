"""Local HTTP callback server for OAuth 2.0 redirect capture.

Uses raw ``asyncio.start_server`` — no extra dependencies.
The server listens on ``127.0.0.1`` for a single GET request at
``/oauth/callback``, extracts the ``code`` and ``state`` query params,
validates the state, serves a success HTML page to the browser, then
shuts down.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

_PORT_RANGE = (19750, 19850)

_SUCCESS_HTML = b"""\
HTTP/1.1 200 OK\r
Content-Type: text/html; charset=utf-8\r
Connection: close\r
\r
<!DOCTYPE html>
<html>
<head><title>Authorization Successful</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px">
<h1>&#10004; Authorization successful</h1>
<p>You can close this tab and return to DeepCLI.</p>
</body>
</html>"""

_ERROR_HTML = b"""\
HTTP/1.1 400 Bad Request\r
Content-Type: text/html; charset=utf-8\r
Connection: close\r
\r
<!DOCTYPE html>
<html>
<head><title>Authorization Failed</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px">
<h1>&#10008; Authorization failed</h1>
<p>%s</p>
</body>
</html>"""


@dataclass
class CallbackHandle:
    """Handle returned by :func:`run_callback_server`.

    Provides the port and a method to await the authorization code.
    """

    port: int
    _future: asyncio.Future[str]
    _server: asyncio.Server

    async def wait_for_code(self, timeout: float = 120.0) -> str:
        """Wait for the browser callback and return the authorization code.

        Raises :class:`TimeoutError` if the callback is not received
        within *timeout* seconds.
        """
        try:
            return await asyncio.wait_for(self._future, timeout=timeout)
        finally:
            self._server.close()
            await self._server.wait_closed()


async def run_callback_server(
    expected_state: str,
    *,
    port_range: tuple[int, int] = _PORT_RANGE,
) -> CallbackHandle:
    """Start a one-shot HTTP server to capture an OAuth callback.

    Args:
        expected_state: The ``state`` parameter sent in the authorization
            request.  The callback **must** include the same value or it
            is rejected (CSRF protection).
        port_range: ``(low, high)`` inclusive range for random port
            selection.

    Returns:
        A :class:`CallbackHandle` with the bound port and a future
        for the authorization code.
    """
    loop = asyncio.get_running_loop()
    code_future: asyncio.Future[str] = loop.create_future()

    async def _handle_connection(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            # Read remaining headers (discard them).
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break

            # Parse request line: "GET /oauth/callback?code=xxx&state=yyy HTTP/1.1"
            parts = request_line.decode("utf-8", errors="replace").split()
            if len(parts) < 2:
                writer.write(_error_response("Invalid request"))
                return

            method, path = parts[0], parts[1]
            if method != "GET":
                writer.write(_error_response("Method not allowed"))
                return

            parsed = urlparse(path)
            if parsed.path != "/oauth/callback":
                writer.write(_error_response("Not found"))
                return

            params = parse_qs(parsed.query)
            code_values = params.get("code", [])
            state_values = params.get("state", [])
            error_values = params.get("error", [])

            # Handle OAuth error response.
            if error_values:
                desc = params.get("error_description", [error_values[0]])[0]
                writer.write(_error_response(f"OAuth error: {desc}"))
                if not code_future.done():
                    code_future.set_exception(
                        OAuthCallbackError(f"Authorization denied: {desc}")
                    )
                return

            if not code_values:
                writer.write(_error_response("Missing 'code' parameter"))
                return

            if not state_values or state_values[0] != expected_state:
                writer.write(_error_response("State mismatch — possible CSRF attack"))
                if not code_future.done():
                    code_future.set_exception(
                        OAuthCallbackError("State parameter mismatch")
                    )
                return

            # Success — deliver the code.
            writer.write(_SUCCESS_HTML)
            if not code_future.done():
                code_future.set_result(code_values[0])

        except asyncio.TimeoutError:
            writer.write(_error_response("Request timeout"))
        except Exception as exc:
            logger.debug("Callback handler error: %s", exc)
            writer.write(_error_response("Internal error"))
        finally:
            await writer.drain()
            writer.close()

    # Try random ports in the range until one works.
    ports = list(range(port_range[0], port_range[1] + 1))
    random.shuffle(ports)

    last_error: OSError | None = None
    for port in ports:
        try:
            server = await asyncio.start_server(
                _handle_connection, "127.0.0.1", port
            )
            logger.info("OAuth callback server listening on 127.0.0.1:%d", port)
            return CallbackHandle(port=port, _future=code_future, _server=server)
        except OSError as exc:
            last_error = exc
            continue

    raise OSError(
        f"Could not bind callback server on any port in {port_range}: {last_error}"
    )


def _error_response(message: str) -> bytes:
    """Build an error HTTP response with the message embedded."""
    return _ERROR_HTML.replace(b"%s", message.encode("utf-8"))


class OAuthCallbackError(Exception):
    """Authorization callback reported an error."""
