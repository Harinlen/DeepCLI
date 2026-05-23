"""FastAPI app for the local Access Router edge."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import kernel
from kernel.access_router.router import AccessRouter, RouteUnavailable
from kernel.access_router.schemas import (
    DeliverTurnRequest,
    RuntimeAcpRequest,
    RuntimePing,
    RuntimePong,
    RuntimeRegisterRequest,
)
from kernel.core.protocol.acp.schemas.runtime import (
    RuntimeRestartRequest,
    RuntimeRestartResponse,
    RuntimeStatusRequest,
    RuntimeStatusResponse,
)

_BOOT_TIME = time.time()
_LOCAL_MANAGEMENT_TARGETS = {"agents", "gateways", "mcp", "global", "flags", "secrets"}
_RUNTIME_CONTROL_METHODS = {
    "_mustang.agent/runtime/status",
    "_mustang.agent/runtime/restart",
}


def create_app(router: AccessRouter | None = None, *, resource_home: str | None = None) -> FastAPI:
    """Create a minimal local Access Router app."""
    app = FastAPI(title="DeepCLI Access Router")
    token = os.environ.get("MUSTANG_ACCESS_ROUTER_TOKEN") or secrets.token_urlsafe(32)
    session_token = os.environ.get("MUSTANG_ACCESS_ROUTER_SESSION_TOKEN")
    resource_home_path = Path(resource_home) if resource_home else None
    app.state.router = router or AccessRouter(
        auth_token=token,
        resource_home=resource_home_path,
    )
    app.state.resource_home = resource_home_path

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "name": "deepcli-access-router",
            "version": kernel.__version__,
            "boot_time": _BOOT_TIME,
        }

    @app.get("/ready")
    async def ready() -> dict[str, object]:
        active = len(app.state.router.registered_agents())
        return {"ready": active > 0, "registered_agents": active}

    @app.get("/access/readiness")
    async def access_readiness() -> dict[str, object]:
        """Return the CLI/launcher readiness contract for the local router."""
        primary = app.state.router.route_status("primary")
        primary_registered = primary.status == "registered"
        return {
            "process_ready": True,
            "hub_ready": True,
            "primary_registered": primary_registered,
            "default_route_ready": primary_registered,
            "platform_bindings_active": False,
            "registered_agents": len(app.state.router.registered_agents()),
            "route_status": primary.model_dump(),
        }

    @app.get("/registered_agents")
    async def registered_agents() -> dict[str, object]:
        return {"agents": [agent.model_dump() for agent in app.state.router.registered_agents()]}

    @app.get("/route_status/{agent_id}")
    async def route_status(agent_id: str) -> dict[str, object]:
        return app.state.router.route_status(agent_id).model_dump()

    @app.websocket("/session")
    async def session(ws: WebSocket) -> None:
        if session_token and not _session_token_matches(ws, session_token):
            await ws.close(code=1008)
            return
        await ws.accept()
        initialized = False
        try:
            while True:
                payload = await ws.receive_json()
                request_id = payload.get("id")
                try:
                    method = payload.get("method")
                    if method != "initialize" and not initialized:
                        raise ProtocolNotInitialized("initialize must be sent first")
                    result = await _route_session_payload(app.state.router, payload, ws)
                    if method == "initialize":
                        initialized = True
                    if not await _send_json_or_closed(
                        ws,
                        {"jsonrpc": "2.0", "id": request_id, "result": result},
                    ):
                        return
                except RouteUnavailable as exc:
                    if not await _send_json_or_closed(
                        ws,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": "route_unavailable", "message": str(exc)},
                        },
                    ):
                        return
                except MethodNotFound as exc:
                    if not await _send_json_or_closed(
                        ws,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": f"Method not found: {exc}"},
                        },
                    ):
                        return
                except ProtocolNotInitialized as exc:
                    if not await _send_json_or_closed(
                        ws,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32600, "message": str(exc)},
                        },
                    ):
                        return
                except WebSocketDisconnect:
                    raise
                except Exception as exc:
                    if not await _send_json_or_closed(
                        ws,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": type(exc).__name__, "message": str(exc)},
                        },
                    ):
                        return
        except WebSocketDisconnect:
            return

    @app.websocket("/runtime")
    async def runtime(ws: WebSocket) -> None:
        await ws.accept()
        try:
            payload = await ws.receive_json()
            if payload.get("method") != "_mustang.router/register_runtime":
                await ws.send_json({"ok": False, "error": "expected register_runtime"})
                return
            request = RuntimeRegisterRequest.model_validate(payload.get("params", {}))
            connection = _RuntimeWebSocketClient(ws)
            result = app.state.router.register_runtime(
                request,
                connection.deliver_turn,
                connection.deliver_acp,
            )
            connection.set_activity_callback(
                lambda: app.state.router.touch_runtime(result.connection_id)
            )
            await ws.send_json({"ok": True, "result": result.model_dump()})
            try:
                await connection.wait_closed()
            finally:
                app.state.router.unregister_runtime(result.connection_id)
        except WebSocketDisconnect:
            return

    return app


async def _route_session_payload(
    router: AccessRouter,
    payload: dict[str, Any],
    client_ws: WebSocket,
) -> dict[str, object]:
    method = payload.get("method")
    params = payload.get("params", payload)
    if not isinstance(params, dict):
        params = {}
    if method in {None, "_mustang.client/turn"}:
        request = DeliverTurnRequest.model_validate(params)
        return await router.deliver_turn(request, _client_request_proxy(client_ws))
    if isinstance(method, str) and method in _RUNTIME_CONTROL_METHODS:
        return await _route_runtime_control_payload(method, params)
    resource_home = getattr(client_ws.app.state, "resource_home", None)
    if isinstance(method, str) and resource_home is not None:
        local_result = await _route_local_management_payload(
            router=router,
            resource_home=resource_home,
            method=method,
            params=params,
        )
        if local_result is not None:
            return local_result
    acp_request = RuntimeAcpRequest(
        agent_id=str(params.get("agent_id") or params.get("agentId") or "primary"),
        method=str(method),
        params={
            str(key): value for key, value in params.items() if key not in {"agent_id", "agentId"}
        },
        session_id=str(params["session_id"]) if "session_id" in params else None,
        request_id=payload.get("id"),
        idempotency_key=_idempotency_key(params),
    )
    return await router.deliver_acp(acp_request, _client_request_proxy(client_ws))


async def _route_runtime_control_payload(
    method: str,
    params: dict[str, Any],
) -> dict[str, object]:
    """Handle supervisor runtime control methods owned by the local router path."""
    socket_path = os.getenv("MUSTANG_SUPERVISOR_CONTROL_SOCKET", "")
    token = os.getenv("MUSTANG_SUPERVISOR_CONTROL_TOKEN", "")
    if not socket_path or not token:
        raise RuntimeError("Supervisor control is not available")

    from kernel.supervisor.control import request_control

    if method == "_mustang.agent/runtime/status":
        RuntimeStatusRequest.model_validate(params)
        status = await asyncio.to_thread(request_control, socket_path, token, "status", {})
        return RuntimeStatusResponse(status=dict(status)).model_dump(by_alias=True)
    restart = RuntimeRestartRequest.model_validate(params)
    status = await asyncio.to_thread(
        request_control,
        socket_path,
        token,
        "restart_runtime",
        {"reason": restart.reason},
    )
    return RuntimeRestartResponse(status=dict(status)).model_dump(by_alias=True)


async def _route_local_management_payload(
    *,
    router: AccessRouter,
    resource_home: Path,
    method: str,
    params: dict[str, Any],
) -> dict[str, object] | None:
    """Handle ResourceStore management methods owned by the Access Router."""
    from kernel.access_router.gateway_commands import GatewayCommandService
    from kernel.access_router.repository import AccessRouterRepository
    from kernel.agent_hub.manager.command_surface import AgentCommandService
    from kernel.agent_hub.manager.manager import AgentManager
    from kernel.agents.mustang.mcp.command_surface import MCPCommandService
    from kernel.core.flags import FlagManager
    from kernel.core.protocol.acp.routing import REQUEST_DISPATCH
    from kernel.core.secrets import SecretManager
    from kernel.core.storage.global_commands import GlobalResourceCommandService

    spec = REQUEST_DISPATCH.get(method)
    if spec is None or spec.target not in _LOCAL_MANAGEMENT_TARGETS:
        return None

    repo: AccessRouterRepository | None = None
    manager: AgentManager | None = None
    flags: FlagManager | None = None
    secrets_manager: SecretManager | None = None
    try:
        handler: object
        if spec.target == "global":
            handler = GlobalResourceCommandService(resource_home)
        elif spec.target == "flags":
            flags = FlagManager(resource_home=resource_home)
            await flags.initialize()
            handler = flags
        elif spec.target == "secrets":
            secrets_manager = SecretManager(home=resource_home)
            await secrets_manager.startup()
            handler = secrets_manager
        elif spec.target == "mcp":
            handler = MCPCommandService(resource_home)
        else:
            repo = AccessRouterRepository.open(resource_home)
            if spec.target == "gateways":
                handler = GatewayCommandService(repo)
            else:
                manager = AgentManager(
                    home=resource_home,
                    route_status_reader=router.route_status,
                )
                manager.startup()
                handler = AgentCommandService(
                    manager=manager,
                    gateway_repository=repo,
                    router=router,
                )
        request_params = spec.params_type.model_validate(params)
        result = await spec.handler(handler, None, request_params)  # type: ignore[arg-type]
        return result.model_dump(by_alias=True)
    finally:
        if secrets_manager is not None:
            secrets_manager.close()
        if flags is not None:
            flags.close()
        if manager is not None:
            manager.close()
        if repo is not None:
            repo.close()


def _idempotency_key(params: dict[str, Any]) -> str | None:
    if "idempotency_key" in params:
        return str(params["idempotency_key"])
    meta = params.get("_meta")
    if isinstance(meta, dict) and "mustang.agent/clientTurnId" in meta:
        return str(meta["mustang.agent/clientTurnId"])
    return None


def _client_request_proxy(
    client_ws: WebSocket,
) -> Callable[[str, dict[str, object]], Awaitable[dict[str, Any]]]:
    request_counter = 0

    async def _proxy(method: str, params: dict[str, object]) -> dict[str, Any]:
        nonlocal request_counter
        if method.startswith("__notify__:"):
            if not await _send_json_or_closed(
                client_ws,
                {
                    "jsonrpc": "2.0",
                    "method": method.removeprefix("__notify__:"),
                    "params": params,
                },
            ):
                raise RuntimeError("client websocket closed")
            return {}
        request_counter += 1
        request_id = request_counter
        if not await _send_json_or_closed(
            client_ws,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        ):
            raise RuntimeError("client websocket closed")
        response: dict[str, Any] = await client_ws.receive_json()
        if response.get("id") != request_id:
            raise RuntimeError("client request response id mismatch")
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    return _proxy


async def _send_json_or_closed(ws: WebSocket, payload: dict[str, Any]) -> bool:
    """Send a JSON payload unless the peer has already closed the websocket."""
    try:
        await ws.send_json(payload)
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if "close message has been sent" in str(exc):
            return False
        raise


def _session_token_matches(ws: WebSocket, token: str) -> bool:
    query_token = ws.query_params.get("token")
    if query_token == token:
        return True
    header = ws.headers.get("authorization", "")
    return header == f"Bearer {token}"


class _RuntimeWebSocketClient:
    """Request/response helper for one connected Agent Runtime."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._lock = asyncio.Lock()
        self._counter = 0
        self._closed = asyncio.Event()
        self._on_activity: Callable[[], None] | None = None

    def set_activity_callback(self, callback: Callable[[], None]) -> None:
        """Record a callback for application-observable runtime traffic."""
        self._on_activity = callback

    async def deliver_turn(
        self,
        request: DeliverTurnRequest,
        client_request_proxy: Callable[[str, dict[str, object]], Awaitable[dict[str, Any]]]
        | None = None,
    ) -> dict[str, object]:
        async with self._lock:
            self._counter += 1
            request_id = f"turn-{self._counter}"
            await self._ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "_mustang.runtime/deliver_turn",
                    "params": request.model_dump(),
                }
            )
            return await self._receive_runtime_result(request_id, client_request_proxy)

    async def deliver_acp(
        self,
        request: RuntimeAcpRequest,
        client_request_proxy: Callable[[str, dict[str, object]], Awaitable[dict[str, Any]]]
        | None = None,
    ) -> dict[str, object]:
        async with self._lock:
            self._counter += 1
            request_id = f"acp-{self._counter}"
            await self._ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "_mustang.runtime/request",
                    "params": request.model_dump(),
                }
            )
            return await self._receive_runtime_result(request_id, client_request_proxy)

    async def _receive_runtime_result(
        self,
        request_id: str,
        client_request_proxy: Callable[[str, dict[str, object]], Awaitable[dict[str, Any]]] | None,
    ) -> dict[str, object]:
        while True:
            response: dict[str, Any] = await self._ws.receive_json()
            self._touch_activity()
            if await self._handle_router_message(response):
                continue
            if response.get("id") == request_id:
                if "error" in response:
                    error = response["error"]
                    if isinstance(error, dict) and error.get("code") == -32601:
                        raise MethodNotFound(str(error.get("message") or "method not found"))
                    raise RuntimeError(str(error))
                result = response.get("result")
                return result if isinstance(result, dict) else {}
            method = response.get("method")
            runtime_request_id = response.get("id")
            if not isinstance(method, str):
                raise RuntimeError("runtime response id mismatch")
            params = response.get("params")
            if not isinstance(params, dict):
                params = {}
            try:
                if client_request_proxy is None:
                    raise RuntimeError("client request proxy unavailable")
                proxy_method = method if runtime_request_id is not None else f"__notify__:{method}"
                result = await client_request_proxy(
                    proxy_method,
                    {str(key): value for key, value in params.items()},
                )
                if runtime_request_id is None:
                    continue
                await self._ws.send_json(
                    {"jsonrpc": "2.0", "id": runtime_request_id, "result": result}
                )
            except Exception as exc:
                await self._ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": runtime_request_id,
                        "error": {"code": type(exc).__name__, "message": str(exc)},
                    }
                )

    async def wait_closed(self) -> None:
        while not self._closed.is_set():
            async with self._lock:
                try:
                    payload: dict[str, Any] = await asyncio.wait_for(
                        self._ws.receive_json(),
                        timeout=0.1,
                    )
                    self._touch_activity()
                    await self._handle_router_message(payload)
                except TimeoutError:
                    pass
                except WebSocketDisconnect:
                    self.mark_closed()
                except RuntimeError as exc:
                    if "disconnect" in str(exc).lower() or "closed" in str(exc).lower():
                        self.mark_closed()
                    else:
                        raise
            await asyncio.sleep(0)

    def mark_closed(self) -> None:
        self._closed.set()

    def _touch_activity(self) -> None:
        if self._on_activity is not None:
            self._on_activity()

    async def _handle_router_message(self, payload: dict[str, Any]) -> bool:
        """Consume runtime-originated router control messages."""
        if payload.get("method") != "_mustang.router/ping":
            return False
        params = payload.get("params")
        if not isinstance(params, dict):
            params = {}
        ping = RuntimePing.model_validate(params)
        response_id = payload.get("id")
        if response_id is not None:
            await self._ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": response_id,
                    "result": RuntimePong(connection_id=ping.connection_id).model_dump(),
                }
            )
        return True


class MethodNotFound(RuntimeError):
    """Raised when a runtime reports JSON-RPC method-not-found."""


class ProtocolNotInitialized(RuntimeError):
    """Raised when a client sends a session request before initialize."""
