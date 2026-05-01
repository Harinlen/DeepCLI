"""WebSocket message dispatch — turn parsed ClientMessage → side-effects.

The WS receive loop parses each incoming frame into a
:class:`ClientMessage` variant (see
:mod:`daemon.api.client_messages`) and hands it to
:func:`dispatch_client_message` which pattern-matches the variant
and performs the corresponding daemon action: starting a query
task, forwarding a permission response, listing sessions, switching
models, entering plan mode, etc.

Keeping the dispatch table in its own module means the WS handler
can stay focused on connection lifecycle (accept → auth → receive
loop → cleanup), and adding a new :data:`ClientMessage` variant
touches exactly two files (``client_messages.py`` + this one).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

from daemon.api.client_messages import (
    Clear,
    ClientMessage,
    CompactRequest,
    CostQuery,
    DeleteSession,
    Interrupt,
    ListSessions,
    ModelList,
    ModelStatus,
    ModelSwitch,
    PermissionModeRequest,
    PermissionResponseMsg,
    PlanModeRequest,
    TasksQuery,
    UserMessage,
    UserQuestionResponseMsg,
)
from daemon.permissions.modes import PermissionMode
from daemon.api.permission_handler import PermissionHandler
from daemon.api.question_handler import QuestionHandler
from daemon.engine.stream import (
    PermissionRequest,
    PermissionResponse,
    StreamEnd,
    UserQuestion,
    UserQuestionResponse,
)
from daemon.extensions.hooks.base import HookContext, HookEvent
from daemon.extensions.hooks.runner import run_hooks
from daemon.sessions.manager import Session, SessionManager

logger = logging.getLogger(__name__)


async def run_query(
    initiator_ws: WebSocket,
    session: Session,
    content: str,
    perm_handler: PermissionHandler,
    question_handler: QuestionHandler | None = None,
) -> None:
    """Run the orchestrator query with session-aware broadcasting.

    - Normal events (text_delta, tool_call_*, end, error) are
      broadcast to **all** connections on the session.
    - ``PermissionRequest`` is sent only to ``initiator_ws`` (the
      connection that submitted the ``user_message``).
    - Token usage is accumulated in the session's metadata.

    The session's ``query_lock`` ensures only one query runs at a
    time.  A second user_message from another connection waits for
    the lock.

    Args:
        initiator_ws: The connection that triggered this query.
        session: The session to run the query on.
        content: User message text.
        perm_handler: Permission handler for the initiator connection.
    """

    async def permission_callback(perm_req: PermissionRequest) -> PermissionResponse:
        """Send permission_request to initiator, wait for response."""
        waiter = perm_handler.create_waiter(perm_req.request_id)
        await session.send_to(initiator_ws, perm_req)
        return await waiter

    # Fire user_prompt_submit hook — can block or rewrite.
    effective_content = content
    try:
        hook_registry = session.orchestrator.tool_executor._hook_registry
        submit_hooks = hook_registry.get_hooks(HookEvent.USER_PROMPT_SUBMIT)
        if submit_hooks:
            hook_ctx = HookContext(user_text=content)
            hook_result = await run_hooks(submit_hooks, hook_ctx)
            if hook_result.blocked:
                logger.info("user_prompt_submit hook blocked query")
                await session.broadcast(
                    {"type": "error", "message": hook_result.output or "Blocked by hook"}
                )
                return
            if (
                hook_result.modified_input
                and "user_text" in hook_result.modified_input
            ):
                effective_content = hook_result.modified_input["user_text"]
    except Exception:
        logger.exception("Error running user_prompt_submit hooks")

    # Build ask_user callback for AskUserQuestion tool.
    ask_user_cb = None
    if question_handler is not None:
        import uuid as _uuid

        async def ask_user_cb(questions: list[dict]) -> dict:
            req_id = _uuid.uuid4().hex[:12]
            waiter = question_handler.create_waiter(req_id)
            q_event = UserQuestion(request_id=req_id, questions=questions)
            await session.send_to(initiator_ws, q_event)
            resp = await waiter
            return resp.answers

    try:
        async with session.query_lock:
            async for event in session.orchestrator.query(
                effective_content, permission_callback, ask_user=ask_user_cb
            ):
                if isinstance(event, PermissionRequest):
                    # Already sent by the callback — skip broadcast.
                    pass
                else:
                    await session.broadcast(event)

                    # Accumulate token usage on StreamEnd.  Attribute
                    # to the orchestrator's effective model so that
                    # per-model breakdown is correct even after
                    # ``/model switch``.
                    if isinstance(event, StreamEnd):
                        effective = (
                            session.orchestrator.effective_model or session.writer.meta.model
                        )
                        session.writer.update_usage(
                            effective,
                            event.usage.input_tokens,
                            event.usage.output_tokens,
                            cache_creation_tokens=event.usage.cache_creation_tokens,
                            cache_read_tokens=event.usage.cache_read_tokens,
                        )
    except asyncio.CancelledError:
        logger.debug("Query task cancelled (client disconnect)")
    except Exception:
        logger.exception("Unhandled error in query task")
        try:
            await session.broadcast({"type": "error", "message": "Internal query error"})
        except Exception:  # nosec B110 — WS may already be closed
            pass


async def dispatch_client_message(
    msg: ClientMessage,
    *,
    ws: WebSocket,
    session: Session,
    session_manager: SessionManager,
    perm_handler: PermissionHandler,
    query_task: asyncio.Task[None] | None,
    question_handler: QuestionHandler | None = None,
) -> asyncio.Task[None] | None:
    """Route a parsed client message to its handler.

    Centralised ``match`` dispatch — adding a new ``ClientMessage``
    variant mandates a new arm here, and mypy flags missing cases
    against the discriminated union.  Returns the updated
    ``query_task`` (or ``None`` to mean "no change") so the receive
    loop can keep track of the in-flight user query.
    """
    match msg:
        case UserMessage(content=content):
            if not content:
                await ws.send_json({"type": "error", "message": "Empty message content"})
                return None

            # Reject if a query is waiting for permission — accepting
            # would deadlock (await query_task blocks the receive
            # loop, so the permission_response can never arrive).
            if query_task is not None and not query_task.done():
                if perm_handler.has_pending:
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": (
                                "A tool is waiting for permission approval. "
                                "Send a permission_response first."
                            ),
                        }
                    )
                    return None
                try:
                    await asyncio.wait_for(query_task, timeout=300)
                except TimeoutError:
                    logger.warning("Previous query timed out waiting for completion")
                    query_task.cancel()
                    try:
                        await query_task
                    except (asyncio.CancelledError, Exception):
                        pass

            return asyncio.create_task(
                run_query(ws, session, content, perm_handler, question_handler)
            )

        case PermissionResponseMsg(request_id=request_id, decision=decision):
            response = PermissionResponse(
                request_id=request_id,
                decision=decision,
            )
            if not perm_handler.resolve(request_id, response):
                logger.warning(
                    "Permission response for unknown request: %s",
                    request_id,
                )
            return None

        case UserQuestionResponseMsg(request_id=request_id, answers=answers):
            if question_handler is not None:
                resp = UserQuestionResponse(request_id=request_id, answers=answers)
                if not question_handler.resolve(request_id, resp):
                    logger.warning(
                        "Question response for unknown request: %s",
                        request_id,
                    )
            return None

        case Clear():
            await session.orchestrator.clear()
            await session.broadcast({"type": "cleared"})
            return None

        case CompactRequest():
            # Manual /compact — run in foreground (waits for lock).
            async with session.query_lock:
                async for event in session.orchestrator.force_compact():
                    await session.broadcast(event)
            return None

        case ListSessions():
            metas = session_manager.list_sessions()
            await ws.send_json(
                {
                    "type": "sessions_list",
                    "sessions": [m.model_dump() for m in metas],
                }
            )
            return None

        case ModelStatus():
            snap = session.orchestrator.get_provider_snapshot()
            # Find the provider entry to expose its type.
            current_type = ""
            for p in snap["providers"]:
                if p["name"] == snap["current_provider_name"]:
                    current_type = p["type"]
                    break
            await ws.send_json(
                {
                    "type": "model_status_result",
                    "provider_name": snap["current_provider_name"],
                    "provider_type": current_type,
                    "model": snap["current_model"],
                    "is_override": snap["is_override"],
                    "default_provider_name": snap["default_provider_name"],
                }
            )
            return None

        case ModelList():
            snap = session.orchestrator.get_provider_snapshot()
            await ws.send_json(
                {
                    "type": "model_list_result",
                    "current": snap["current_provider_name"],
                    "providers": snap["providers"],
                }
            )
            return None

        case ModelSwitch(provider_name=new_name):
            try:
                session.orchestrator.set_provider_override(new_name)
            except ValueError:
                snap = session.orchestrator.get_provider_snapshot()
                available = [p["name"] for p in snap["providers"]]
                await ws.send_json(
                    {
                        "type": "model_switch_result",
                        "ok": False,
                        "error": f"Provider {new_name!r} not configured",
                        "available": available,
                    }
                )
                return None
            snap = session.orchestrator.get_provider_snapshot()
            await ws.send_json(
                {
                    "type": "model_switch_result",
                    "ok": True,
                    "provider_name": snap["current_provider_name"],
                    "model": snap["current_model"],
                }
            )
            return None

        case CostQuery():
            meta = session.writer.meta
            effective = session.orchestrator.effective_model or meta.model
            await ws.send_json(
                {
                    "type": "cost_info",
                    "session_id": session.session_id,
                    "total_input_tokens": meta.total_input_tokens,
                    "total_output_tokens": meta.total_output_tokens,
                    "provider": meta.provider,
                    "current_model": effective,
                    "model_usage": {name: u.model_dump() for name, u in meta.model_usage.items()},
                }
            )
            return None

        case PlanModeRequest(action=action):
            if action == "enter":
                async with session.query_lock:
                    async for evt in session.orchestrator.enter_plan_mode():
                        await session.broadcast(evt)
            elif action == "exit":
                async with session.query_lock:
                    async for evt in session.orchestrator.exit_plan_mode():
                        await session.broadcast(evt)
            else:  # "status"
                await ws.send_json(
                    {
                        "type": "plan_mode_status",
                        "active": session.orchestrator.in_plan_mode,
                    }
                )
            return None

        case PermissionModeRequest(action=action):
            target = PermissionMode(action)
            async with session.query_lock:
                async for evt in session.orchestrator.set_permission_mode(target):
                    await session.broadcast(evt)
            return None

        case TasksQuery():
            await ws.send_json(
                {
                    "type": "tasks_list",
                    "tasks": session.orchestrator.current_tasks(),
                }
            )
            return None

        case Interrupt():
            if query_task is not None and not query_task.done():
                query_task.cancel()
                try:
                    await query_task
                except (asyncio.CancelledError, Exception):
                    pass
            await ws.send_json({"type": "interrupted"})
            return None

        case DeleteSession(session_id=target_id):
            if not target_id:
                await ws.send_json({"type": "error", "message": "Missing session_id"})
            elif target_id == session.session_id:
                await ws.send_json({"type": "error", "message": "Cannot delete the active session"})
            else:
                success = session_manager.delete(target_id)
                await ws.send_json(
                    {
                        "type": "session_deleted",
                        "session_id": target_id,
                        "success": success,
                    }
                )
            return None


__all__ = ["dispatch_client_message", "run_query"]
