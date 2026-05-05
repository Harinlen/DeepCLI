"""AcpSessionHandler — the ACP ``SessionDispatcher`` implementation.

This is the protocol layer's main loop.  It:

1. Holds per-connection state (``ConnectionContext``, ``ClientSender``).
2. Routes every inbound ``AcpMessage`` to the right handler:
   * ``initialize`` / ``authenticate`` → ``AcpHandshake``
   * ``session/*`` requests → ``SessionHandler`` (session layer)
   * ``session/cancel`` notification → ``SessionHandler.cancel``
   * Inbound response → resolves a pending ``ClientSender`` Future
3. Builds the ``HandlerContext`` injected into every session call.
4. Maps handler results / exceptions → JSON-RPC response frames.
5. On disconnect, cancels all pending outgoing requests and calls
   the session layer's cleanup.

Implements :class:`kernel.routes.stack.SessionDispatcher`
(structural typing — no base class).

ACP-specific contract
---------------------
* ``initialize`` MUST be the first request from any client.
  Any other method before it → ``-32600 Invalid Request``.
* ``authenticate`` is accepted at any point and is always a noop.
* ``session/cancel`` is a notification → no JSON-RPC response.
* ``session/prompt`` cancellation → ``PromptResult(stop_reason="cancelled")``
  as a **success** response, never an error frame.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

import pydantic

from kernel.protocol.acp.codec import (
    AcpInboundNotification,
    AcpInboundRequest,
    AcpInboundResponse,
    AcpMessage,
    AcpOutbound,
    AcpOutboundError,
    AcpOutboundNotification,
    AcpOutboundRequest,
    AcpOutboundResponse,
)
from kernel.protocol.acp.handshake import AcpHandshake
from kernel.protocol.acp.namespaces import MustangMethod
from kernel.protocol.acp.routing import NOTIFICATION_DISPATCH, REQUEST_DISPATCH
from kernel.protocol.acp.schemas.enums import AcpStopReason
from kernel.protocol.acp.schemas.initialize import (
    AuthenticateRequest,
    InitializeRequest,
)
from kernel.protocol.acp.schemas.session import (
    CancelNotification,
    CancelRequestNotification,
    CloseSessionRequest,
    CloseSessionResponse,
    CancelExecutionRequest,
    CancelExecutionResponse,
    ExecutePythonRequest,
    ExecutePythonResponse,
    ExecuteShellRequest,
    ExecuteShellResponse,
    GetUsageRequest,
    GetUsageResponse,
    ListSessionsRequest,
    ListSessionsResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    ResumeSessionRequest,
    ResumeSessionResponse,
    SetSessionModeRequest,
    SetSessionModeResponse,
)
from kernel.protocol.acp.schemas.updates import (
    MustangExecutionUpdateNotification,
    SessionUpdateNotification,
)
from kernel.protocol.interfaces.contracts.connection_context import (
    ConnectionContext,
)
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    REQUEST_CANCELLED,
    InternalError,
    InvalidRequest,
    MethodNotFound,
    RequestCancelled,
)

if TYPE_CHECKING:
    from kernel.connection_auth import AuthContext
    from kernel.module_table import KernelModuleTable
    from kernel.protocol.interfaces.model_handler import ModelHandler
    from kernel.protocol.interfaces.session_handler import SessionHandler

logger = logging.getLogger(__name__)

_HUB_RUNTIME_RETRY_ATTEMPTS = 24
_HUB_RUNTIME_RETRY_DELAY_SECONDS = 0.25
_ROUTER_MODEL_METHODS = frozenset(
    {
        MustangMethod.MODEL_PROFILE_LIST,
        MustangMethod.MODEL_PROVIDER_LIST,
        MustangMethod.MODEL_PROVIDER_ADD,
        MustangMethod.MODEL_PROVIDER_REMOVE,
        MustangMethod.MODEL_PROVIDER_REFRESH,
        MustangMethod.MODEL_SET_CURRENT,
        MustangMethod.MODEL_ADD,
        MustangMethod.MODEL_UPDATE,
    }
)


# ---------------------------------------------------------------------------
# Concrete ClientSender
# ---------------------------------------------------------------------------


class _AcpClientSender:
    """Concrete :class:`~kernel.protocol.interfaces.client_sender.ClientSender`.

    Scoped to one connection.  Holds a reference to the outbound queue
    that ``AcpSessionHandler.dispatch`` drains.  Handlers push messages
    here; the dispatcher yields them back to transport.
    """

    def __init__(self, connection_id: str) -> None:
        self._connection_id = connection_id
        self._queue: asyncio.Queue[AcpOutbound] = asyncio.Queue()
        self._outgoing_in_flight: dict[int, asyncio.Future] = {}
        self._id_counter = itertools.count(1)

    async def notify(self, method: str, params: pydantic.BaseModel) -> None:
        await self._queue.put(AcpOutboundNotification(method=method, params=params))

    async def request(
        self,
        method: str,
        params: pydantic.BaseModel,
        *,
        result_type: type[pydantic.BaseModel],
        timeout: float | None = None,
    ) -> Any:
        req_id = next(self._id_counter)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._outgoing_in_flight[req_id] = fut
        await self._queue.put(AcpOutboundRequest(id=req_id, method=method, params=params))
        try:
            raw = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.CancelledError:
            self._outgoing_in_flight.pop(req_id, None)
            raise
        finally:
            self._outgoing_in_flight.pop(req_id, None)
        return result_type.model_validate(raw)

    def resolve_response(self, req_id: int, result: dict | None, error: dict | None) -> None:
        """Called by the dispatcher when a client response arrives."""
        fut = self._outgoing_in_flight.get(req_id)
        if fut is None or fut.done():
            return
        if error is not None:
            fut.set_exception(InternalError(str(error)))
        else:
            fut.set_result(result or {})

    def pending_request_ids(self) -> list[Any]:
        return list(self._outgoing_in_flight.keys())

    def cancel_all_pending(self) -> None:
        """Abandon all in-flight outgoing requests (used on disconnect)."""
        for fut in self._outgoing_in_flight.values():
            if not fut.done():
                fut.cancel()
        self._outgoing_in_flight.clear()


# ---------------------------------------------------------------------------
# AcpSessionHandler
# ---------------------------------------------------------------------------


class AcpSessionHandler:
    """Process-wide :class:`kernel.routes.stack.SessionDispatcher` for ACP.

    One instance per kernel process.  Per-connection state lives in
    ``_AcpClientSender`` instances (one per connection) and in
    ``ConnectionContext`` objects (also one per connection, held in a
    ``conn_states`` dict keyed by ``connection_id``).
    """

    def __init__(self, module_table: KernelModuleTable) -> None:
        self._module_table = module_table
        self._handshake = AcpHandshake()
        # connection_id → (ConnectionContext, _AcpClientSender)
        self._connections: dict[str, tuple[ConnectionContext, _AcpClientSender]] = {}
        self._incoming_tasks: dict[str | int, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # SessionDispatcher interface
    # ------------------------------------------------------------------

    def dispatch(self, msg: AcpMessage, auth: AuthContext) -> AsyncIterator[AcpOutbound]:
        """Route one inbound message, yield zero-or-more outbound messages."""
        return self._dispatch_impl(msg, auth)

    async def _dispatch_impl(
        self, msg: AcpMessage, auth: AuthContext
    ) -> AsyncIterator[AcpOutbound]:
        conn, sender = self._get_or_create_connection(auth)

        if isinstance(msg, AcpInboundResponse):
            # Client responded to one of our outgoing requests.
            sender.resolve_response(
                msg.id,  # type: ignore[arg-type]
                msg.result,
                msg.error,
            )
            return  # No outbound frame needed.

        if isinstance(msg, AcpInboundNotification):
            async for out in self._handle_notification(msg, conn, sender):
                yield out
            return

        if not isinstance(msg, AcpInboundRequest):
            raise InvalidRequest(f"Unsupported ACP inbound message: {type(msg).__name__}")
        async for out in self._handle_request(msg, conn, sender):
            yield out

        # Drain any messages the handler pushed via sender.notify /
        # sender.request while processing (e.g. session/update chunks).
        while not sender._queue.empty():
            yield sender._queue.get_nowait()

    async def _handle_request(
        self,
        msg: AcpInboundRequest,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> AsyncIterator[AcpOutbound]:
        """Route a request and yield all outbound frames.

        For long-lived handlers (``session/prompt``) the handler runs as
        a concurrent task while this generator continuously drains the
        sender queue.  This is critical: notifications
        (``session/update``) and kernel-initiated requests
        (``session/request_permission``) pushed by the handler must
        reach the client **during** execution, not after — otherwise
        permission round-trips deadlock.
        """
        method = msg.method
        req_id = msg.id

        # Run the handler in a background task so we can drain the
        # sender queue concurrently.
        handler_result: list[pydantic.BaseModel | BaseException] = []

        async def _run() -> None:
            try:
                result_model = await self._route_request(method, msg, conn, sender)
                handler_result.append(result_model)
            except asyncio.CancelledError:
                handler_result.append(RequestCancelled("Request cancelled"))
            except Exception as exc:
                handler_result.append(exc)

        handler_task = asyncio.create_task(_run())
        self._incoming_tasks[req_id] = handler_task

        # Drain the sender queue continuously while the handler runs.
        # Items include session/update notifications, session/request_permission
        # outgoing requests, and any other messages pushed by the handler.
        while not handler_task.done():
            try:
                item = await asyncio.wait_for(sender._queue.get(), timeout=0.05)
                yield item
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                handler_task.cancel()
                raise

        self._incoming_tasks.pop(req_id, None)

        # Handler finished. Drain any notifications pushed between the last
        # queue.get timeout and handler completion before emitting the response;
        # session/load relies on replay updates arriving before its result.
        while not sender._queue.empty():
            yield sender._queue.get_nowait()

        # Emit the response after all currently queued handler notifications.
        if handler_result and isinstance(handler_result[0], BaseException):
            error = handler_result[0]
            if isinstance(error, Exception):
                yield self._make_error_response(req_id, error)
            else:
                yield self._make_error_response(req_id, InternalError(str(error)))
        elif handler_result:
            yield AcpOutboundResponse(
                id=req_id,
                result=handler_result[0],  # type: ignore[arg-type]
            )
        else:
            yield self._make_error_response(req_id, InternalError("handler produced no result"))

        # Defensive final drain for any message enqueued while the response was
        # being constructed.
        while not sender._queue.empty():
            yield sender._queue.get_nowait()

    async def _route_request(
        self,
        method: str,
        msg: AcpInboundRequest,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> pydantic.BaseModel:
        # --- initialize (must be first) ---
        if method == "initialize":
            if conn.initialized:
                raise InvalidRequest("Connection already initialized")
            try:
                init_params = InitializeRequest.model_validate(msg.params)
            except pydantic.ValidationError as exc:
                raise _make_invalid_params(exc)
            return await self._handshake.initialize(conn, init_params)

        # --- guard: must initialize first ---
        if not conn.initialized:
            raise InvalidRequest("Connection not initialized; send 'initialize' first")

        # --- authenticate (noop, always success) ---
        if method == "authenticate":
            try:
                auth_params = AuthenticateRequest.model_validate(msg.params)
            except pydantic.ValidationError as exc:
                raise _make_invalid_params(exc)
            return await self._handshake.authenticate(conn, auth_params)

        # --- session/* and model/* methods ---
        if os.getenv("MUSTANG_AGENT_PROMPT_BACKEND") == "router" and method in {
            "session/new",
            "session/list",
            "session/load",
            "session/resume",
            "session/prompt",
            "session/close",
            "session/set_mode",
            MustangMethod.SESSION_EXECUTE_SHELL,
            MustangMethod.SESSION_EXECUTE_PYTHON,
            MustangMethod.SESSION_CANCEL_EXECUTION,
            MustangMethod.SESSION_GET_USAGE,
            *_ROUTER_MODEL_METHODS,
        }:
            if method in _ROUTER_MODEL_METHODS:
                return await self._route_model_request_through_hub(method, msg, conn)
            if method == "session/new":
                try:
                    new_params = NewSessionRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_new_session_through_hub(new_params, conn)
            if method == "session/list":
                try:
                    list_params = ListSessionsRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_list_sessions_through_hub(list_params, conn)
            if method == "session/load":
                try:
                    load_params = LoadSessionRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_load_session_through_hub(load_params, conn, sender)
            if method == "session/close":
                try:
                    close_params = CloseSessionRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_close_through_hub(close_params, conn)
            if method == "session/resume":
                try:
                    resume_params = ResumeSessionRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_resume_through_hub(resume_params, conn)
            if method == "session/set_mode":
                try:
                    set_mode_params = SetSessionModeRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_set_mode_through_hub(
                    set_mode_params,
                    conn,
                    sender,
                )
            if method == MustangMethod.SESSION_EXECUTE_SHELL:
                try:
                    shell_params = ExecuteShellRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_execute_shell_through_hub(
                    shell_params,
                    conn,
                    sender,
                )
            if method == MustangMethod.SESSION_EXECUTE_PYTHON:
                try:
                    python_params = ExecutePythonRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_execute_python_through_hub(
                    python_params,
                    conn,
                    sender,
                )
            if method == MustangMethod.SESSION_CANCEL_EXECUTION:
                try:
                    cancel_execution_params = CancelExecutionRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_cancel_execution_through_hub(
                    cancel_execution_params,
                    conn,
                )
            if method == MustangMethod.SESSION_GET_USAGE:
                try:
                    usage_params = GetUsageRequest.model_validate(msg.params)
                except pydantic.ValidationError as exc:
                    raise _make_invalid_params(exc)
                return await self._route_get_usage_through_hub(usage_params, conn)
            try:
                prompt_params = PromptRequest.model_validate(msg.params)
            except pydantic.ValidationError as exc:
                raise _make_invalid_params(exc)
            return await self._route_prompt_through_hub(prompt_params, conn, sender)

        spec = REQUEST_DISPATCH.get(method)
        if spec is None:
            raise MethodNotFound(f"Method not found: {method!r}")

        try:
            params = spec.params_type.model_validate(msg.params)
        except pydantic.ValidationError as exc:
            raise _make_invalid_params(exc)

        ctx = HandlerContext(conn=conn, sender=sender, request_id=msg.id)
        handler = self._get_handler_for(spec.target)
        return await spec.handler(handler, ctx, params)

    async def _route_new_session_through_hub(
        self,
        params: NewSessionRequest,
        conn: ConnectionContext,
    ) -> NewSessionResponse:
        payload = await self._route_agent_contract_through_hub(
            contract="agent.session_new",
            params=params.model_dump(by_alias=True),
            session_id=None,
            conn=conn,
        )
        session_id = str(payload["sessionId"])
        conn.bound_session_id = session_id
        return NewSessionResponse(session_id=session_id)

    async def _route_list_sessions_through_hub(
        self,
        params: ListSessionsRequest,
        conn: ConnectionContext,
    ) -> ListSessionsResponse:
        payload = await self._route_agent_contract_through_hub(
            contract="agent.session_list",
            params=params.model_dump(by_alias=True),
            session_id=None,
            conn=conn,
        )
        return ListSessionsResponse.model_validate(payload)

    async def _route_load_session_through_hub(
        self,
        params: LoadSessionRequest,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> LoadSessionResponse:
        payload = await self._route_agent_contract_through_hub(
            contract="agent.session_load",
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
        )
        updates = payload.get("updates", [])
        if isinstance(updates, list):
            for raw_update in updates:
                notification = SessionUpdateNotification.model_validate(raw_update)
                await sender.notify("session/update", notification)
        conn.bound_session_id = params.session_id
        return LoadSessionResponse(
            config_options=payload.get("configOptions"),
            modes=payload.get("modes"),
            meta=payload.get("_meta") or payload.get("meta"),
        )

    async def _route_prompt_through_hub(
        self,
        params: PromptRequest,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> PromptResponse:
        payload = await self._route_agent_prompt_through_hub(
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
            sender=sender,
        )
        updates = payload.get("updates", [])
        if isinstance(updates, list):
            for raw_update in updates:
                notification = SessionUpdateNotification.model_validate(raw_update)
                await sender.notify("session/update", notification)
        return PromptResponse(
            stop_reason=cast(AcpStopReason, str(payload.get("stopReason", "end_turn"))),
            meta=payload.get("_meta") or payload.get("meta"),
        )

    async def _route_set_mode_through_hub(
        self,
        params: SetSessionModeRequest,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> SetSessionModeResponse:
        payload = await self._route_agent_contract_through_hub(
            contract="agent.set_mode",
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
        )
        updates = payload.get("updates", [])
        if isinstance(updates, list):
            for raw_update in updates:
                notification = SessionUpdateNotification.model_validate(raw_update)
                await sender.notify("session/update", notification)
        return SetSessionModeResponse(meta=payload.get("_meta") or payload.get("meta"))

    async def _route_execute_shell_through_hub(
        self,
        params: ExecuteShellRequest,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> ExecuteShellResponse:
        payload = await self._route_agent_contract_through_hub(
            contract="agent.execute_shell",
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
        )
        await self._emit_execution_updates(payload, sender)
        return ExecuteShellResponse(
            exit_code=int(payload.get("exitCode", 0)),
            cancelled=bool(payload.get("cancelled", False)),
            meta=payload.get("_meta") or payload.get("meta"),
        )

    async def _route_execute_python_through_hub(
        self,
        params: ExecutePythonRequest,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> ExecutePythonResponse:
        payload = await self._route_agent_contract_through_hub(
            contract="agent.execute_python",
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
        )
        await self._emit_execution_updates(payload, sender)
        return ExecutePythonResponse(
            exit_code=int(payload.get("exitCode", 0)),
            cancelled=bool(payload.get("cancelled", False)),
            meta=payload.get("_meta") or payload.get("meta"),
        )

    async def _route_cancel_execution_through_hub(
        self,
        params: CancelExecutionRequest,
        conn: ConnectionContext,
    ) -> CancelExecutionResponse:
        await self._route_agent_contract_through_hub(
            contract="agent.cancel_execution",
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
        )
        return CancelExecutionResponse()

    async def _route_get_usage_through_hub(
        self,
        params: GetUsageRequest,
        conn: ConnectionContext,
    ) -> GetUsageResponse:
        payload = await self._route_agent_contract_through_hub(
            contract="agent.get_usage",
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
        )
        return GetUsageResponse.model_validate(payload)

    async def _emit_execution_updates(
        self,
        payload: dict[str, Any],
        sender: _AcpClientSender,
    ) -> None:
        updates = payload.get("executionUpdates", [])
        if not isinstance(updates, list):
            return
        for raw_update in updates:
            notification = MustangExecutionUpdateNotification.model_validate(raw_update)
            await sender.notify(MustangMethod.SESSION_EXECUTION_UPDATE, notification)

    async def _route_agent_prompt_through_hub(
        self,
        *,
        params: dict[str, Any],
        session_id: str,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> dict[str, Any]:
        hub_endpoint = getattr(self._module_table, "agent_hub_endpoint", None)
        if not hub_endpoint:
            raise InternalError("Agent Hub endpoint is not available")

        import websockets

        from kernel.agents import (
            CallerIdentity,
            CallerIdentityKind,
            HubFrame,
            HubFrameType,
            RouterFrame,
            RouterFrameKind,
            RouterTarget,
        )

        router_frame = RouterFrame(
            frame_id=f"access-route-{uuid.uuid4().hex}",
            kind=RouterFrameKind.USER_MESSAGE,
            source="access:native",
            target=RouterTarget(),
            caller=CallerIdentity(
                kind=CallerIdentityKind.ACCESS,
                subject_id=conn.auth.connection_id,
                connection_id=conn.auth.connection_id,
            ),
            session_id=session_id,
            payload={"method": "agent.prompt"},
        )
        request = HubFrame(
            frame_id=f"access-agent-{uuid.uuid4().hex}",
            frame_type=HubFrameType.REQUEST,
            contract="agent.prompt",
            payload={
                "routerFrame": router_frame.model_dump(),
                "params": params,
            },
        )
        async with websockets.connect(hub_endpoint) as ws:
            await ws.send(request.to_json_bytes())
            while True:
                raw = await ws.recv()
                frame = HubFrame.from_json_bytes(raw)
                if frame.frame_type is HubFrameType.REQUEST and frame.contract == "client.request":
                    response = await self._handle_hub_client_request(frame, sender)
                    await ws.send(response.to_json_bytes())
                    continue
                if frame.payload.get("ok") is not True:
                    error = frame.payload.get("error", "agent.prompt failed")
                    message = frame.payload.get("message")
                    detail = f"{error}: {message}" if message else str(error)
                    raise InternalError(detail)
                return frame.payload

    async def _handle_hub_client_request(
        self,
        frame: Any,
        sender: _AcpClientSender,
    ) -> Any:
        from kernel.agents import HubFrame, HubFrameType
        from kernel.protocol.acp.schemas.permission import (
            RequestPermissionRequest,
            RequestPermissionResponse,
        )
        from kernel.protocol.acp.schemas.updates import SessionUpdateNotification

        method = str(frame.payload.get("method", ""))
        params = frame.payload.get("params", {})
        try:
            if method == "session/update":
                await sender.notify(
                    "session/update",
                    SessionUpdateNotification.model_validate(params),
                )
                payload = {"ok": True, "result": {}}
            elif method == "session/request_permission":
                result = await sender.request(
                    method,
                    RequestPermissionRequest.model_validate(params),
                    result_type=RequestPermissionResponse,
                )
                payload = {"ok": True, "result": result.model_dump(by_alias=True)}
            else:
                raise InternalError(f"unsupported runtime client request: {method}")
        except Exception as exc:
            payload = {
                "ok": False,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        return HubFrame(
            frame_id=f"{frame.frame_id}:response",
            frame_type=HubFrameType.RESPONSE,
            contract=frame.contract,
            correlation_id=frame.frame_id,
            payload=payload,
        )

    async def _route_resume_through_hub(
        self,
        params: ResumeSessionRequest,
        conn: ConnectionContext,
    ) -> ResumeSessionResponse:
        payload = await self._route_agent_contract_through_hub(
            contract="agent.resume",
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
        )
        conn.bound_session_id = params.session_id
        return ResumeSessionResponse(
            config_options=payload.get("configOptions"),
            modes=payload.get("modes"),
            meta=payload.get("_meta") or payload.get("meta"),
        )

    async def _route_close_through_hub(
        self,
        params: CloseSessionRequest,
        conn: ConnectionContext,
    ) -> CloseSessionResponse:
        await self._route_agent_contract_through_hub(
            contract="agent.close",
            params=params.model_dump(by_alias=True),
            session_id=params.session_id,
            conn=conn,
        )
        if conn.bound_session_id == params.session_id:
            conn.bound_session_id = None
        return CloseSessionResponse()

    async def _route_model_request_through_hub(
        self,
        method: str,
        msg: AcpInboundRequest,
        conn: ConnectionContext,
    ) -> pydantic.BaseModel:
        spec = REQUEST_DISPATCH.get(method)
        if spec is None or spec.target != "model":
            raise MethodNotFound(f"Method not found: {method!r}")
        try:
            params = spec.params_type.model_validate(msg.params)
        except pydantic.ValidationError as exc:
            raise _make_invalid_params(exc)
        payload = await self._route_agent_contract_through_hub(
            contract="agent.model_request",
            params={
                "method": method,
                "params": params.model_dump(by_alias=True),
            },
            session_id=conn.bound_session_id,
            conn=conn,
        )
        return spec.result_type.model_validate(payload)

    async def _route_agent_contract_through_hub(
        self,
        *,
        contract: str,
        params: dict[str, Any],
        session_id: str | None,
        conn: ConnectionContext,
    ) -> dict[str, Any]:
        hub_endpoint = getattr(self._module_table, "agent_hub_endpoint", None)
        if not hub_endpoint:
            raise InternalError("Agent Hub endpoint is not available")

        from kernel.agent_hub import request_hub
        from kernel.agents import (
            CallerIdentity,
            CallerIdentityKind,
            HubFrame,
            HubFrameType,
            RouterFrame,
            RouterFrameKind,
            RouterTarget,
        )

        router_frame = RouterFrame(
            frame_id=f"access-route-{uuid.uuid4().hex}",
            kind=RouterFrameKind.USER_MESSAGE,
            source="access:native",
            target=RouterTarget(),
            caller=CallerIdentity(
                kind=CallerIdentityKind.ACCESS,
                subject_id=conn.auth.connection_id,
                connection_id=conn.auth.connection_id,
            ),
            session_id=session_id,
            payload={"method": contract},
        )
        response = None
        for attempt in range(1, _HUB_RUNTIME_RETRY_ATTEMPTS + 1):
            response = await request_hub(
                hub_endpoint,
                HubFrame(
                    frame_id=f"access-agent-{uuid.uuid4().hex}",
                    frame_type=HubFrameType.REQUEST,
                    contract=contract,
                    payload={
                        "routerFrame": router_frame.model_dump(),
                        "params": params,
                    },
                ),
            )
            if response.payload.get("error") != "runtime_not_registered":
                break
            if attempt == _HUB_RUNTIME_RETRY_ATTEMPTS:
                break
            await asyncio.sleep(_HUB_RUNTIME_RETRY_DELAY_SECONDS)

        if response is None:  # pragma: no cover - loop always runs
            raise InternalError(f"{contract} failed")

        if response.payload.get("ok") is not True:
            error = response.payload.get("error", f"{contract} failed")
            message = response.payload.get("message")
            detail = f"{error}: {message}" if message else str(error)
            raise InternalError(detail)
        return response.payload

    async def _handle_notification(
        self,
        msg: AcpInboundNotification,
        conn: ConnectionContext,
        sender: _AcpClientSender,
    ) -> AsyncIterator[AcpOutbound]:
        method = msg.method
        if method == "$/cancel_request":
            self._cancel_incoming_request(msg)
            return

        if os.getenv("MUSTANG_AGENT_PROMPT_BACKEND") == "router" and method == "session/cancel":
            try:
                cancel_params = CancelNotification.model_validate(msg.params)
            except pydantic.ValidationError:
                logger.warning("Invalid params for notification %r — ignoring", method)
                return
            try:
                await self._route_agent_contract_through_hub(
                    contract="agent.cancel",
                    params=cancel_params.model_dump(by_alias=True),
                    session_id=cancel_params.session_id,
                    conn=conn,
                )
            except Exception:
                logger.exception("Error routing notification %r through Hub — ignoring", method)
            return

        spec = NOTIFICATION_DISPATCH.get(method)
        if spec is None:
            # ACP: unknown notifications (incl. ``$/`` prefixed) MUST be
            # silently ignored per JSON-RPC 2.0 spec.
            logger.debug("Ignoring unknown notification: %r", method)
            return

        if not conn.initialized and method != "session/cancel":
            logger.warning(
                "Notification %r received before initialize — ignoring",
                method,
            )
            return

        try:
            notification_params = spec.params_type.model_validate(msg.params)
        except pydantic.ValidationError:
            # Notification params errors are silently dropped.
            logger.warning("Invalid params for notification %r — ignoring", method)
            return

        session_handler = self._get_session_handler()
        ctx = HandlerContext(conn=conn, sender=sender, request_id=None)
        try:
            await spec.handler(session_handler, ctx, notification_params)
        except Exception:
            # JSON-RPC 2.0: notifications have no response — errors must
            # be swallowed here, never propagated to transport (which
            # would close the connection with 1011).
            logger.exception("Error handling notification %r — ignoring", method)
            return

        # Drain any messages the notification handler may have pushed.
        while not sender._queue.empty():
            yield sender._queue.get_nowait()

    def _cancel_incoming_request(self, msg: AcpInboundNotification) -> None:
        try:
            params = CancelRequestNotification.model_validate(msg.params)
        except pydantic.ValidationError:
            logger.warning("Invalid params for notification '$/cancel_request' — ignoring")
            return

        task = self._incoming_tasks.get(params.request_id)
        if task is not None and not task.done():
            task.cancel()

    # ------------------------------------------------------------------
    # on_disconnect
    # ------------------------------------------------------------------

    async def on_disconnect(self, auth: AuthContext) -> None:
        """Cancel all pending futures, clean up connection state, and notify
        the session layer so it can remove the sender from live sessions."""
        entry = self._connections.pop(auth.connection_id, None)
        if entry is None:
            return
        _, sender = entry
        sender.cancel_all_pending()

        # Notify SessionManager — connections are tracked per-session there.
        try:
            session_handler = self._get_session_handler()
            await session_handler.on_disconnect(auth.connection_id)
        except Exception:
            # SessionManager may not be loaded (degraded mode) — swallow.
            logger.debug("conn=%s: session layer on_disconnect skipped", auth.connection_id)

        logger.info("conn=%s ACP connection cleaned up", auth.connection_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_connection(
        self, auth: AuthContext
    ) -> tuple[ConnectionContext, _AcpClientSender]:
        if auth.connection_id not in self._connections:
            conn = ConnectionContext(auth=auth)
            sender = _AcpClientSender(auth.connection_id)
            self._connections[auth.connection_id] = (conn, sender)
        return self._connections[auth.connection_id]

    def _get_handler_for(self, target: str) -> Any:
        """Return the handler object for the given routing target.

        Raises ``InternalError`` if the required subsystem is not loaded
        (e.g. it failed to start — kernel degraded mode).
        """
        if target == "session":
            return self._get_session_handler()
        if target == "model":
            return self._get_model_handler()
        if target == "secrets":
            return self._get_secrets_handler()
        raise InternalError(f"Unknown routing target: {target!r}")

    def _get_session_handler(self) -> SessionHandler:
        """Retrieve the SessionHandler from the module table.

        Raises ``InternalError`` if SessionManager is not loaded
        (e.g. it failed to start — kernel degraded mode).
        """
        try:
            from kernel.session import SessionManager

            return cast(Any, self._module_table.get(SessionManager))  # type: ignore[type-abstract]
        except KeyError:
            raise InternalError("SessionManager subsystem is not available")

    def _get_model_handler(self) -> ModelHandler:
        """Retrieve the ModelHandler from the module table.

        Raises ``InternalError`` if LLMManager is not loaded
        (e.g. it failed to start — kernel degraded mode).
        """
        try:
            from kernel.llm import LLMManager

            return self._module_table.get(LLMManager)
        except KeyError:
            raise InternalError("LLMManager subsystem is not available")

    def _get_secrets_handler(self) -> Any:
        """Retrieve SecretManager from the module table.

        SecretManager is a bootstrap service (not a Subsystem) so it
        lives on ``module_table.secrets`` rather than in the subsystem
        dict.  Returns ``Any`` because routing.py handler wrappers
        already know the concrete type.
        """
        sm = self._module_table.secrets
        if sm is None:
            raise InternalError("SecretManager is not available")
        return sm

    def _make_error_response(self, req_id: str | int, exc: Exception) -> AcpOutboundError:
        code = getattr(exc, "code", INTERNAL_ERROR)
        if code == INTERNAL_ERROR:
            # Never leak internal details; log them instead.
            logger.error(
                "Internal error handling request id=%s",
                req_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            message = "Internal error"
        elif code == INVALID_PARAMS:
            message = "Invalid params"
        elif code == INVALID_REQUEST:
            message = str(exc)
        elif code == METHOD_NOT_FOUND:
            message = str(exc)
        elif code == REQUEST_CANCELLED:
            message = str(exc)
        else:
            message = str(exc)
        return AcpOutboundError(id=req_id, code=code, message=message)


def _make_invalid_params(exc: pydantic.ValidationError) -> Exception:
    """Strip raw param values from a ValidationError before surfacing it."""
    from kernel.protocol.interfaces.errors import InvalidParams

    return InvalidParams(f"Invalid params ({exc.error_count()} error(s))")
