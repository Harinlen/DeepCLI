"""WS ``/session`` — transport layer implementation.

Only concerns here: accept, authenticate, resolve the active
protocol stack via :class:`kernel.routes.flags.TransportFlags`,
run the fixed ``recv → decode → dispatch → encode → send`` loop,
handle disconnects.  Protocol decoding and session dispatching
live behind the :class:`kernel.routes.stack.ProtocolStack`
interface — see ``docs/kernel/subsystems/transport.md`` for the
design rationale.

Every log line carries ``conn=<id>`` so a single connection's full
lifecycle can be grepped from structured logs without joining on
timestamps.  The ``connection_id`` is generated **here** in
transport (not in :class:`kernel.connection_auth.ConnectionAuthenticator`) so
the same identifier appears in every log line from accept onward,
including the pre-auth diagnostics that fire when credentials are
rejected.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.datastructures import QueryParams

from kernel.connection_auth import AuthError, ConnectionAuthenticator
from kernel.module_table import KernelModuleTable
from kernel.routes.flags import TransportFlags
from kernel.routes.stack import ProtocolError, ProtocolStack, create_stack

router = APIRouter()
logger = logging.getLogger(__name__)


# RFC 6455 close codes used by this transport.  4000-4999 is the
# private-use range; only 4003 is consumed right now and is
# reserved for authentication failure so clients can distinguish
# "bad credentials" from transport-level issues.
_CLOSE_NORMAL = 1000
_CLOSE_INTERNAL_ERROR = 1011
_CLOSE_AUTH_FAILED = 4003


@router.websocket("/session")
async def session_ws(ws: WebSocket) -> None:
    """Top-level transport entry point for the ``/session`` endpoint.

    Steps:

    1. ``accept`` the WebSocket (we need an accepted socket before
       we can send a ``close`` frame with a specific code, so
       authentication happens *after* accept even though failure
       leads straight to close).
    2. Generate ``connection_id`` and record the peer address for
       the audit trail.
    3. Extract credentials from query params, call
       :meth:`kernel.connection_auth.ConnectionAuthenticator.authenticate`,
       close with 4003 on any failure.
    4. Resolve the active protocol stack via
       :class:`TransportFlags` and hand off to
       :func:`_run_transport_loop`.
    5. Translate disconnect / unexpected exceptions into the
       appropriate close code and log line.
    """
    await ws.accept()

    connection_id = uuid4().hex
    remote_addr = _format_remote_addr(ws)
    logger.info("conn=%s accepted from %s", connection_id, remote_addr)

    module_table: KernelModuleTable = ws.app.state.module_table
    try:
        authenticator = module_table.get(ConnectionAuthenticator)
    except KeyError:
        logger.error(
            "conn=%s ConnectionAuthenticator subsystem not loaded — closing %d",
            connection_id,
            _CLOSE_AUTH_FAILED,
        )
        await ws.close(code=_CLOSE_AUTH_FAILED, reason="authentication failed")
        return

    # --- Step 3: authentication handshake ---
    try:
        credential, credential_type = _extract_credentials(ws.query_params)
    except _MissingCredentials:
        logger.info(
            "conn=%s missing credentials — closing %d",
            connection_id,
            _CLOSE_AUTH_FAILED,
        )
        await ws.close(code=_CLOSE_AUTH_FAILED, reason="authentication failed")
        return

    try:
        auth_ctx = await authenticator.authenticate(
            connection_id=connection_id,
            credential=credential,
            credential_type=credential_type,
            remote_addr=remote_addr,
        )
    except AuthError:
        # AuthError's message is deliberately the fixed string
        # "authentication failed" — we echo only the connection id
        # into our own log so operators can correlate, and never
        # leak which specific check failed.
        logger.info(
            "conn=%s auth failed — closing %d",
            connection_id,
            _CLOSE_AUTH_FAILED,
        )
        await ws.close(code=_CLOSE_AUTH_FAILED, reason="authentication failed")
        return

    logger.info(
        "conn=%s authenticated type=%s local=%s",
        connection_id,
        auth_ctx.credential_type,
        auth_ctx.is_local,
    )

    # --- Step 4: resolve protocol stack ---
    transport_flags = cast(TransportFlags, module_table.flags.get_section("transport"))
    stack: ProtocolStack[Any] = create_stack(transport_flags.stack, module_table)
    logger.info("conn=%s stack=%s started", connection_id, transport_flags.stack)

    # --- Step 5: main loop + disconnect translation ---
    try:
        await _run_transport_loop(ws, auth_ctx, stack)
        # Loop returns cleanly only on normal close triggered by
        # the receive path (currently: never, recv_text keeps
        # blocking until the peer disconnects).  Keep the close
        # call here anyway so future well-behaved dispatchers that
        # voluntarily end the session get a clean 1000 code.
        await ws.close(code=_CLOSE_NORMAL)
        logger.info("conn=%s closed normally", connection_id)
    except WebSocketDisconnect as exc:
        logger.info(
            "conn=%s disconnected by peer code=%s",
            connection_id,
            exc.code,
        )
    except Exception:
        logger.exception("conn=%s transport crashed", connection_id)
        await _try_close(ws, _CLOSE_INTERNAL_ERROR, "internal error")
    finally:
        # Give the dispatcher a chance to cancel in-flight tasks and
        # unbind from any active sessions, regardless of how the
        # connection ended.
        await stack.dispatcher.on_disconnect(auth_ctx)


async def _run_transport_loop(
    ws: WebSocket,
    auth_ctx: Any,
    stack: ProtocolStack[Any],
) -> None:
    """Concurrent recv + dispatch + send transport loop.

    Unlike the earlier serial ``recv → dispatch → send`` design, this
    version keeps **reading** inbound frames while a long-lived handler
    (``session/prompt``) runs.  Without this, ``session/request_permission``
    deadlocks: the kernel sends the permission request via the sender
    queue, but the client's response can't be read because recv is
    blocked on the dispatch generator.

    Architecture:

    1. ``_bg_recv`` — reads raw WS frames into ``inbound`` queue.
    2. Main loop — pulls from ``inbound``, decodes, dispatches each
       frame in its own ``asyncio.Task``.  Multiple dispatches run
       concurrently (e.g. the prompt handler + a permission response).
    3. Each dispatch task writes outbound frames to ``outbound`` queue.
    4. ``_bg_send`` — drains ``outbound`` queue to WS.

    Decode errors produce an error frame and continue.
    """
    codec = stack.codec
    dispatcher = stack.dispatcher

    outbound: asyncio.Queue[str] = asyncio.Queue()
    dispatch_tasks: set[asyncio.Task[None]] = set()

    async def _bg_send() -> None:
        """Drain outbound queue → WebSocket."""
        while True:
            text = await outbound.get()
            await ws.send_text(text)

    async def _dispatch_one(msg: Any) -> None:
        """Run one dispatch to completion, queuing all outbound frames."""
        try:
            async for response in dispatcher.dispatch(msg, auth_ctx):
                await outbound.put(codec.encode(response))
        except Exception:
            logger.exception("dispatch task crashed")

    send_task = asyncio.create_task(_bg_send(), name="transport-send")
    try:
        while True:
            raw = await ws.receive_text()

            try:
                msg = codec.decode(raw)
            except ProtocolError as exc:
                await outbound.put(codec.encode_error(exc))
                continue

            # Each inbound frame gets its own dispatch task so
            # long-lived handlers don't block recv.
            task = asyncio.create_task(_dispatch_one(msg), name="dispatch")
            dispatch_tasks.add(task)
            task.add_done_callback(dispatch_tasks.discard)
    finally:
        send_task.cancel()
        for t in dispatch_tasks:
            t.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass


# --------------------------------------------------------------------
# Credential extraction
# --------------------------------------------------------------------


class _MissingCredentials(Exception):
    """Raised when neither ``token`` nor ``password`` is present.

    Kept private to this module — transport translates it into a
    4003 close, there's no reason for other code to catch it.
    """


def _extract_credentials(
    params: QueryParams,
) -> tuple[str, Literal["token", "password"]]:
    """Pull the credential + type out of a WebSocket query string.

    Behavior:

    - ``?token=xxx`` → ``(xxx, "token")``
    - ``?password=xxx`` → ``(xxx, "password")``
    - both given → **token wins** — holding the local token file
      is a stronger locality claim than knowing the password, so
      we pick the stronger evidence
    - neither → :class:`_MissingCredentials`

    The empty-string case (``?token=``) is treated the same as
    "not provided", because an empty credential can never pass
    :meth:`kernel.connection_auth.ConnectionAuthenticator.authenticate` and
    failing early gives a clearer log line than "authenticate
    rejected an empty string".
    """
    token = params.get("token") or None
    password = params.get("password") or None

    if token is not None:
        return token, "token"
    if password is not None:
        return password, "password"
    raise _MissingCredentials()


def _format_remote_addr(ws: WebSocket) -> str:
    """``host:port`` string for ``AuthContext.remote_addr`` and logs.

    ASGI may report ``ws.client`` as ``None`` for synthetic /
    test-harness sockets; fall back to a literal ``"unknown"``
    rather than raising, because neither the audit log nor
    :class:`kernel.connection_auth.AuthContext` should ever crash on missing
    peer info.  Remember that ``remote_addr`` is **not** a
    locality signal — it is retained purely for diagnostics (see
    ``docs/kernel/subsystems/connection_authenticator.md``).
    """
    if ws.client is None:
        return "unknown"
    return f"{ws.client.host}:{ws.client.port}"


async def _try_close(ws: WebSocket, code: int, reason: str) -> None:
    """Best-effort close that swallows its own errors.

    Used only from the exception handler — if the socket is already
    half-dead (e.g. the peer vanished), a second exception here
    would mask the one we're trying to log.  The original error
    has already been recorded with :meth:`logger.exception`, so the
    safe thing is to attempt the close and move on.
    """
    try:
        await ws.close(code=code, reason=reason)
    except Exception:  # pragma: no cover - defensive
        logger.debug("close(%d) failed", code, exc_info=True)
