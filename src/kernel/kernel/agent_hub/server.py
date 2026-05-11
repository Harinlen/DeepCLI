"""Internal Agent Hub websocket transport.

This is not a FastAPI route.  It is the Batch C internal loopback transport
used by Access Agent and Agent Runtime processes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import websockets
from pydantic import ValidationError
from websockets.asyncio.server import Server, ServerConnection

from kernel.agent_hub.hub import AgentHub
from kernel.agent_hub.contracts import (
    AGENT_RUNTIME_FORWARDED_CONTRACTS,
    AGENT_RUNTIME_STREAMING_CONTRACTS,
    AgentRegistrationRequest,
    AgentRuntimeRecord,
    HubFrame,
    HubFrameType,
    RouterFrame,
)
from kernel.agents.mustang.runtime import request_runtime


class AgentHubWebSocketServer:
    """Small Agent Hub internal websocket server."""

    def __init__(
        self,
        hub: AgentHub,
        *,
        registration_tokens: Mapping[str, str] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.hub = hub
        self.registration_tokens = dict(registration_tokens or {})
        self.host = host
        self.port = port
        self._server: Server | None = None

    @property
    def endpoint(self) -> str:
        """Return the ws:// endpoint after startup."""

        if self._server is None or not self._server.sockets:
            raise RuntimeError("hub server is not started")
        port = self._server.sockets[0].getsockname()[1]
        return f"ws://{self.host}:{port}"

    async def start(self) -> None:
        """Start the internal websocket server."""

        self._server = await websockets.serve(self._handle, self.host, self.port)

    async def stop(self) -> None:
        """Stop the internal websocket server."""

        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, ws: ServerConnection) -> None:
        async for raw in ws:
            request = HubFrame.from_json_bytes(raw)
            response = await self._dispatch(request, ws)
            await ws.send(response.to_json_bytes())

    async def _dispatch(self, frame: HubFrame, ws: ServerConnection | None = None) -> HubFrame:
        try:
            payload = await self._dispatch_payload(frame, ws)
            return HubFrame(
                frame_id=f"{frame.frame_id}:response",
                frame_type=HubFrameType.RESPONSE,
                contract=frame.contract,
                correlation_id=frame.frame_id,
                payload=payload,
            )
        except Exception as exc:
            return HubFrame(
                frame_id=f"{frame.frame_id}:error",
                frame_type=HubFrameType.RESPONSE,
                contract=frame.contract,
                correlation_id=frame.frame_id,
                payload={
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
            )

    async def _dispatch_payload(
        self,
        frame: HubFrame,
        access_ws: ServerConnection | None = None,
    ) -> dict[str, Any]:
        if frame.contract == "hub.readiness":
            return {"ok": True, "readiness": self.hub.readiness()}
        if frame.contract == "agent.register":
            return self._register_agent(frame)
        if frame.contract == "router.frame":
            router_frame = RouterFrame.model_validate(frame.payload["routerFrame"])
            return {
                "ok": True,
                "targetAgentId": self.hub.router.resolve_target(router_frame),
            }
        if frame.contract in AGENT_RUNTIME_STREAMING_CONTRACTS:
            return await self._forward_agent_contract(frame, frame.contract, access_ws=access_ws)
        if frame.contract in AGENT_RUNTIME_FORWARDED_CONTRACTS:
            return await self._forward_agent_contract(frame, frame.contract)
        raise ValueError(f"unknown hub contract: {frame.contract}")

    def _register_agent(self, frame: HubFrame) -> dict[str, Any]:
        try:
            request = AgentRegistrationRequest.model_validate(frame.payload)
        except ValidationError:
            raise

        expected_secret = self.registration_tokens.get(request.agent_id)
        if expected_secret is None or expected_secret != request.registration_token.secret:
            raise PermissionError("invalid registration token")

        from kernel.agent_hub.contracts import AgentStatus

        self.hub.manager.upsert_runtime_record(
            AgentRuntimeRecord(
                agent_id=request.agent_id,
                runtime_kind=request.runtime_kind,
                websocket_endpoint=request.websocket_endpoint,
                status=AgentStatus.idle,
            )
        )
        if request.agent_id == "primary":
            snapshot = self.hub.manager.routing_snapshot(revision=1)
            self.hub.router.update_snapshot(snapshot)
        return {"ok": True, "agentId": request.agent_id}

    async def _forward_agent_contract(
        self,
        frame: HubFrame,
        contract: str,
        *,
        access_ws: ServerConnection | None = None,
    ) -> dict[str, Any]:
        router_frame = RouterFrame.model_validate(frame.payload["routerFrame"])
        routed = self.hub.router.route_message(router_frame)
        if routed is None:
            return {"ok": False, "error": "route_not_found"}

        record = self.hub.manager.get_runtime_record(routed.target_agent_id)
        if record is None or record.websocket_endpoint is None:
            return {"ok": False, "error": "runtime_not_registered"}

        response = await request_runtime(
            record.websocket_endpoint,
            HubFrame(
                frame_id=f"{frame.frame_id}:runtime",
                frame_type=HubFrameType.REQUEST,
                contract=contract,
                correlation_id=frame.frame_id,
                payload={
                    "agentId": routed.target_agent_id,
                    "params": frame.payload.get("params", {}),
                },
            ),
            timeout=_runtime_timeout_for_contract(contract, frame),
            client_request_handler=(
                (lambda request: _proxy_client_request(access_ws, request))
                if access_ws is not None
                else None
            ),
        )
        if response.payload.get("ok") is not True:
            return response.payload
        payload = dict(response.payload)
        payload["targetAgentId"] = routed.target_agent_id
        return payload


def _runtime_timeout_for_contract(contract: str, frame: HubFrame) -> float | None:
    if contract in AGENT_RUNTIME_STREAMING_CONTRACTS:
        return None
    if contract != "agent.tools_request":
        return 5

    params = frame.payload.get("params")
    if not isinstance(params, dict):
        return 120
    method = params.get("method")
    method_params = params.get("params")
    if method == "_mustang.agent/web_fetch/set_backend" and isinstance(method_params, dict):
        if method_params.get("runSetup") is True:
            return 600
    return 120


async def _proxy_client_request(
    access_ws: ServerConnection | None,
    request: HubFrame,
) -> HubFrame:
    """Proxy a runtime-originated client request to the Access Agent."""

    if access_ws is None:
        return HubFrame(
            frame_id=f"{request.frame_id}:error",
            frame_type=HubFrameType.RESPONSE,
            contract=request.contract,
            correlation_id=request.frame_id,
            payload={"ok": False, "error": "access_connection_unavailable"},
        )
    await access_ws.send(request.to_json_bytes())
    raw = await access_ws.recv()
    response = HubFrame.from_json_bytes(raw)
    return response


async def request_hub(endpoint: str, frame: HubFrame) -> HubFrame:
    """Send one frame to an Agent Hub websocket endpoint."""

    async with websockets.connect(endpoint) as ws:
        await ws.send(frame.to_json_bytes())
        raw = await ws.recv()
    return HubFrame.from_json_bytes(raw)
