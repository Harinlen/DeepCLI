"""FastAPI app for the local Access Router edge."""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from kernel.access_router.router import AccessRouter, RouteUnavailable
from kernel.access_router.schemas import DeliverTurnRequest, RuntimeAcpRequest, RuntimePing, RuntimeRegisterRequest

_RUNTIME_HEARTBEAT_INTERVAL_SECONDS = 5.0


def create_app(router: AccessRouter | None = None, *, resource_home: str | None = None) -> FastAPI:
    """Create a minimal local Access Router app."""
    app = FastAPI(title="DeepCLI Access Router")
    token = os.environ.get("MUSTANG_ACCESS_ROUTER_TOKEN") or secrets.token_urlsafe(32)
    session_token = os.environ.get("MUSTANG_ACCESS_ROUTER_SESSION_TOKEN")
    app.state.router = router or AccessRouter(
        auth_token=token,
        resource_home=Path(resource_home) if resource_home else None,
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"ok": True}

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
        return {
            "agents": [
                agent.model_dump()
                for agent in app.state.router.registered_agents()
            ]
        }

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
                    await ws.send_json({"jsonrpc": "2.0", "id": request_id, "result": result})
                except RouteUnavailable as exc:
                    await ws.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": "route_unavailable", "message": str(exc)},
                        }
                    )
                except MethodNotFound as exc:
                    await ws.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": f"Method not found: {exc}"},
                        }
                    )
                except ProtocolNotInitialized as exc:
                    await ws.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32600, "message": str(exc)},
                        }
                    )
                except Exception as exc:
                    await ws.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": type(exc).__name__, "message": str(exc)},
                        }
                    )
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
            await ws.send_json({"ok": True, "result": result.model_dump()})
            heartbeat = asyncio.create_task(
                _heartbeat_runtime(app.state.router, connection, result.connection_id)
            )
            try:
                await connection.wait_closed()
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
        except WebSocketDisconnect:
            return

    return app


async def _heartbeat_runtime(
    router: AccessRouter,
    connection: "_RuntimeWebSocketClient",
    connection_id: str,
) -> None:
    while True:
        await asyncio.sleep(_RUNTIME_HEARTBEAT_INTERVAL_SECONDS)
        try:
            await connection.ping()
            router.ping(RuntimePing(connection_id=connection_id))
        except Exception:
            connection.mark_closed()
            return


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
    acp_request = RuntimeAcpRequest(
        agent_id=str(params.get("agent_id") or params.get("agentId") or "primary"),
        method=str(method),
        params={
            str(key): value
            for key, value in params.items()
            if key not in {"agent_id", "agentId"}
        },
        session_id=str(params["session_id"]) if "session_id" in params else None,
        request_id=payload.get("id"),
        idempotency_key=_idempotency_key(params),
    )
    return await router.deliver_acp(acp_request, _client_request_proxy(client_ws))


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
            await client_ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "method": method.removeprefix("__notify__:"),
                    "params": params,
                }
            )
            return {}
        request_counter += 1
        request_id = request_counter
        await client_ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        response: dict[str, Any] = await client_ws.receive_json()
        if response.get("id") != request_id:
            raise RuntimeError("client request response id mismatch")
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    return _proxy


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

    async def deliver_turn(
        self,
        request: DeliverTurnRequest,
        client_request_proxy: Callable[[str, dict[str, object]], Awaitable[dict[str, Any]]] | None = None,
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
        client_request_proxy: Callable[[str, dict[str, object]], Awaitable[dict[str, Any]]] | None = None,
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
        await self._closed.wait()

    def mark_closed(self) -> None:
        self._closed.set()

    async def ping(self) -> None:
        async with self._lock:
            self._counter += 1
            request_id = f"ping-{self._counter}"
            await self._ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "_mustang.runtime/ping",
                    "params": {},
                }
            )
            response: dict[str, Any] = await self._ws.receive_json()
            if response.get("id") != request_id:
                raise RuntimeError("runtime ping response id mismatch")
            if "error" in response:
                raise RuntimeError(str(response["error"]))


class MethodNotFound(RuntimeError):
    """Raised when a runtime reports JSON-RPC method-not-found."""


class ProtocolNotInitialized(RuntimeError):
    """Raised when a client sends a session request before initialize."""
