"""WebSocket endpoint for client-daemon communication.

Protocol (JSON messages over WebSocket):

  Client -> Daemon:
    {"type": "user_message", "content": "..."}
    {"type": "permission_response", "request_id": "...", "allowed": true|false}
    {"type": "clear"}
    {"type": "compact_request"}
    {"type": "list_sessions"}
    {"type": "delete_session", "session_id": "..."}
    {"type": "cost_query"}
    {"type": "model_status"}
    {"type": "model_list"}
    {"type": "model_switch", "provider_name": "..."}

  Daemon -> Client:
    {"type": "session_id", "session_id": "..."}
    {"type": "text_delta", "content": "..."}
    {"type": "thinking_delta", "content": "..."}
    {"type": "tool_call_start", "tool_call_id": "...", "tool_name": "...", "arguments": {...}}
    {"type": "tool_call_result", "tool_call_id": "...", "tool_name": "...", "output": "...", "is_error": false}
    {"type": "permission_request", "request_id": "...", "tool_name": "...", "arguments": {...}}
    {"type": "end", "usage": {"input_tokens": N, "output_tokens": N}}
    {"type": "error", "message": "..."}
    {"type": "compact", "summary_preview": "...", "messages_summarized": N}
    {"type": "sessions_list", "sessions": [...]}
    {"type": "session_deleted", "session_id": "...", "success": true|false}
    {"type": "cost_info", "total_input_tokens": N, "total_output_tokens": N, ...}
    {"type": "model_status_result", "provider_name": "...", "model": "...", ...}
    {"type": "model_list_result", "current": "...", "providers": [...]}
    {"type": "model_switch_result", "ok": true|false, "model": "...", "error": "...", "available": [...]}

Session model:
  - Each WS connection is bound to exactly one session.
  - ``/ws?token=xxx`` creates a new session.
  - ``/ws?token=xxx&session_id=abc`` joins (or resumes) an existing session.
  - Multiple connections can subscribe to the same session; events
    are broadcast to all of them.
  - ``permission_request`` is sent only to the connection that
    initiated the query (unicast), not broadcast.

Architecture note: permission requests require a round-trip — the
daemon sends ``permission_request``, then blocks until the client
sends ``permission_response``.  This means the WS receive loop must
stay active while the orchestrator query is running.  We use a
concurrent design: the query runs in a background task and
communicates permission requests via asyncio.Future objects that
the receive loop resolves.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

import time

from daemon.api.client_messages import ValidationError, parse_client_message
from daemon.api.permission_handler import PermissionHandler
from daemon.api.question_handler import QuestionHandler
from daemon.api.ws_dispatch import dispatch_client_message
from daemon.auth import verify_token
from daemon.extensions.hooks.base import HookContext, HookEvent
from daemon.extensions.hooks.runner import run_hooks
from daemon.sessions.manager import Session, SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()


async def _fire_session_hook(
    session: Session,
    event: HookEvent,
    ctx: HookContext,
) -> None:
    """Fire a session-lifecycle hook, swallowing errors."""
    try:
        hook_registry = session.orchestrator.tool_executor._hook_registry
        hooks = hook_registry.get_hooks(event)
        if hooks:
            await run_hooks(hooks, ctx)
    except Exception:
        logger.exception("Error running %s hook", event.value)


def _resolve_session(
    session_manager: SessionManager,
    session_id: str | None,
) -> Session:
    """Find or create a session for a new WebSocket connection.

    Args:
        session_manager: The daemon's session manager.
        session_id: Client-supplied session ID (``None`` to create
            a new session).

    Returns:
        An active :class:`Session`.

    Raises:
        FileNotFoundError: If ``session_id`` refers to a non-existent
            persisted session.
        ValueError: If the persisted session is too large to load.
    """
    if session_id:
        # Try in-memory first, then disk.
        session = session_manager.get(session_id)
        if session is not None:
            return session
        return session_manager.resume(session_id)

    return session_manager.create()


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str = Query(default=""),
    session_id: str | None = Query(default=None),
) -> None:
    """Main WebSocket handler.

    Binds the connection to a session (new or existing), then enters
    the receive loop.  Events from the orchestrator are broadcast to
    all connections on the session.

    The ``Orchestrator`` is per-session (not global).
    """
    await ws.accept()

    expected_token: str = ws.app.state.auth_token  # type: ignore[union-attr]
    if not token or not verify_token(token, expected_token):
        await ws.close(code=4001, reason="Invalid auth token")
        return

    session_manager: SessionManager = ws.app.state.session_manager  # type: ignore[union-attr]

    # Resolve or create session.
    try:
        session = _resolve_session(session_manager, session_id)
    except FileNotFoundError:
        await ws.send_json({"type": "error", "message": f"Session not found: {session_id}"})
        await ws.close(code=4004, reason="Session not found")
        return
    except ValueError as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close(code=4005, reason="Session too large")
        return

    # Wire the on_entry callback so the orchestrator writes to the
    # transcript.  This is idempotent — multiple connections on the
    # same session share the same writer.
    session.orchestrator.set_transcript_writer(session.write_entry)

    session.add_connection(ws)

    # Tell the client which session it is on.
    await ws.send_json({"type": "session_id", "session_id": session.session_id})

    perm_handler = PermissionHandler()
    question_handler = QuestionHandler()
    query_task: asyncio.Task[None] | None = None
    is_resume = session_id is not None
    connect_time = time.monotonic()

    logger.info("WebSocket client connected to session %s", session.session_id[:8])

    # Fire session_start hook.
    await _fire_session_hook(
        session,
        HookEvent.SESSION_START,
        HookContext(
            session_id=session.session_id,
            cwd=str(session.orchestrator.cwd) if hasattr(session.orchestrator, "cwd") else None,
            is_resume=is_resume,
        ),
    )

    try:
        while True:
            try:
                data: dict[str, Any] = await ws.receive_json()
            except ValueError:
                # Malformed JSON from client — reject but keep connection.
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            # Parse once; validation errors become a single error
            # frame.  See ``client_messages.ClientMessage`` for the
            # exhaustive union.
            try:
                msg = parse_client_message(data)
            except ValidationError:
                raw_type = data.get("type", "")
                await ws.send_json(
                    {"type": "error", "message": f"Unknown message type: {raw_type}"}
                )
                continue

            new_query = await dispatch_client_message(
                msg,
                ws=ws,
                session=session,
                session_manager=session_manager,
                perm_handler=perm_handler,
                query_task=query_task,
                question_handler=question_handler,
            )
            if new_query is not None:
                query_task = new_query

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from session %s", session.session_id[:8])
    except Exception:
        logger.exception("Unexpected error in WebSocket handler")
        try:
            await ws.send_json({"type": "error", "message": "Internal server error"})
            await ws.close(code=1011)
        except Exception:  # nosec B110
            pass
    finally:
        perm_handler.cancel_all()
        question_handler.cancel_all()
        if query_task is not None and not query_task.done():
            query_task.cancel()
        session.remove_connection(ws)
        # Fire session_end hook.
        await _fire_session_hook(
            session,
            HookEvent.SESSION_END,
            HookContext(
                session_id=session.session_id,
                duration_s=time.monotonic() - connect_time,
            ),
        )
