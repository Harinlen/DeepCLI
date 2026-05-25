"""Access Router route table and local turn delivery."""

from __future__ import annotations

import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.access_router.adapters.base import AccessAdapter, AdapterInboundMessage, AdapterReply
from kernel.access_router.idempotency import IdempotencyStore
from kernel.access_router.repository import AccessRouterRepository, ChannelBinding
from kernel.access_router.schemas import (
    DeliverTurnRequest,
    RegisteredAgent,
    RouteStatus,
    RuntimeAcpRequest,
    RuntimePing,
    RuntimePong,
    RuntimeRegisterRequest,
    RuntimeRegisterResult,
)
from kernel.kernel_bus import BusServiceRecord, BusTopologySnapshot

ClientRequestProxy = Callable[[str, dict[str, object]], Awaitable[dict[str, Any]]]
RuntimeHandler = Callable[
    [DeliverTurnRequest, ClientRequestProxy | None], Awaitable[dict[str, object]]
]
RuntimeAcpHandler = Callable[
    [RuntimeAcpRequest, ClientRequestProxy | None], Awaitable[dict[str, object]]
]
RuntimeHandlerInput = Callable[..., Awaitable[dict[str, object]]]
RuntimeAcpHandlerInput = Callable[..., Awaitable[dict[str, object]]]


class RouteUnavailable(RuntimeError):
    """Raised when no fresh runtime route exists."""


class ChannelBindingUnavailable(RuntimeError):
    """Raised when an adapter message has no enabled channel binding."""


@dataclass(slots=True)
class _RuntimeConnection:
    agent_id: str
    connection_id: str
    pid: int
    protocol_version: int
    handler: RuntimeHandler
    acp_handler: RuntimeAcpHandler | None
    last_seen: float


class AccessRouter:
    """Message hot path owned by the Access Router process."""

    def __init__(
        self,
        *,
        auth_token: str,
        protocol_version: int = 1,
        stale_timeout_seconds: float = 15.0,
        resource_home: Path | None = None,
        repository: AccessRouterRepository | None = None,
    ) -> None:
        self._auth_token = auth_token
        self._protocol_version = protocol_version
        self._stale_timeout_seconds = stale_timeout_seconds
        self._routes: dict[str, _RuntimeConnection] = {}
        self._resource_records: dict[str, BusServiceRecord] = {}
        self._topology_revision = 1
        self._idempotency = IdempotencyStore()
        self._repository = repository or (
            AccessRouterRepository.open(resource_home) if resource_home is not None else None
        )
        self.agent_hub_forward_count = 0

    @property
    def auth_token(self) -> str:
        """Runtime registration token for manager-owned local Agent processes."""
        return self._auth_token

    def register_runtime(
        self,
        request: RuntimeRegisterRequest,
        handler: RuntimeHandlerInput,
        acp_handler: RuntimeAcpHandlerInput | None = None,
    ) -> RuntimeRegisterResult:
        """Register or replace a runtime route."""
        if request.auth_token != self._auth_token:
            raise PermissionError("invalid runtime auth token")
        if request.protocol_version != self._protocol_version:
            raise ValueError("unsupported runtime protocol version")
        if request.role != "agent_runtime":
            raise ValueError("runtime registration role must be agent_runtime")
        connection_id = str(uuid.uuid4())
        self._routes[request.agent_id] = _RuntimeConnection(
            agent_id=request.agent_id,
            connection_id=connection_id,
            pid=request.pid,
            protocol_version=request.protocol_version,
            handler=_normalize_turn_handler(handler),
            acp_handler=_normalize_acp_handler(acp_handler) if acp_handler is not None else None,
            last_seen=time.monotonic(),
        )
        self._topology_revision += 1
        return RuntimeRegisterResult(
            agent_id=request.agent_id,
            connection_id=connection_id,
            status="registered",
        )

    def unregister_runtime(self, connection_id: str) -> None:
        """Remove the runtime route owned by a closed WebSocket connection."""
        for agent_id, route in list(self._routes.items()):
            if route.connection_id == connection_id:
                self._routes.pop(agent_id, None)
                self._topology_revision += 1
                return

    def ping(self, ping: RuntimePing) -> RuntimePong:
        """Update a runtime heartbeat by connection id."""
        route = self._route_by_connection_id(ping.connection_id)
        route.last_seen = time.monotonic()
        return RuntimePong(connection_id=ping.connection_id)

    def pong(self, pong: RuntimePong) -> None:
        """Accept runtime pong messages as a heartbeat update."""
        if pong.ok:
            self._route_by_connection_id(pong.connection_id).last_seen = time.monotonic()

    async def deliver_turn(
        self,
        request: DeliverTurnRequest,
        client_request_proxy: ClientRequestProxy | None = None,
    ) -> dict[str, object]:
        """Route one local client turn to the target runtime."""
        key = request.idempotency_key or f"{request.agent_id}:{request.client_turn_id}"
        cached = self._idempotency.get(key)
        if cached is not None:
            return cached.result
        route = self._fresh_route(request.agent_id)
        result = await route.handler(request, client_request_proxy)
        self._idempotency.put(key, status="completed", result=result)
        return result

    async def deliver_acp(
        self,
        request: RuntimeAcpRequest,
        client_request_proxy: ClientRequestProxy | None = None,
    ) -> dict[str, object]:
        """Route one ACP request to the target runtime."""
        key = request.idempotency_key
        if key is not None:
            cached = self._idempotency.get(key)
            if cached is not None:
                if client_request_proxy is not None and request.method == "session/prompt":
                    updates = cached.result.get("updates")
                    if isinstance(updates, list):
                        for update in updates:
                            if isinstance(update, dict):
                                await client_request_proxy("__notify__:session/update", update)
                return cached.result
        route = self._fresh_route(request.agent_id)
        if route.acp_handler is None:
            raise RouteUnavailable(f"runtime route for {request.agent_id!r} does not support ACP")
        result = await route.acp_handler(request, client_request_proxy)
        if key is not None:
            self._idempotency.put(key, status="completed", result=result)
        return result

    async def start_adapter(self, adapter: AccessAdapter) -> None:
        """Start an Access Router-owned adapter and record status."""
        try:
            await adapter.start()
        except Exception as exc:
            self._record_adapter_status(adapter.adapter_id, "failed", str(exc))
            raise
        self._record_adapter_status(adapter.adapter_id, "running")

    async def handle_adapter_inbound(
        self,
        adapter: AccessAdapter,
        message: AdapterInboundMessage,
    ) -> dict[str, object]:
        """Route one external adapter message through the same runtime hot path."""
        if adapter.adapter_id != message.adapter_id:
            raise ValueError("adapter id mismatch")
        inbound_key = f"inbound:{message.adapter_id}:{message.external_message_id}"
        existing = self._repository.get_idempotency(inbound_key) if self._repository else None
        if existing is not None:
            return {
                "status": "duplicate",
                "reply_sent": False,
                "result": existing.result,
            }

        binding = self._resolve_binding(message.adapter_id, message.channel_key)
        if binding is None:
            raise ChannelBindingUnavailable(
                f"no enabled binding for {message.adapter_id}:{message.channel_key}"
            )

        client_turn_id = f"{message.adapter_id}:{message.external_message_id}"
        request = DeliverTurnRequest(
            agent_id=binding.target_agent_id,
            session_id=binding.target_session_id
            or f"adapter:{message.adapter_id}:{message.channel_key}",
            client_turn_id=client_turn_id,
            prompt=message.text,
            idempotency_key=f"turn:{message.adapter_id}:{message.external_message_id}",
        )
        result = await self.deliver_turn(request)
        text = _reply_text(result)
        outbound_key = f"outbound:{message.adapter_id}:{message.external_message_id}"
        reply_sent = await self.send_adapter_reply(
            adapter,
            channel_key=message.channel_key,
            outbound_reply_id=outbound_key,
            text=text,
        )
        self._put_idempotency(
            key=inbound_key,
            direction="inbound",
            adapter_id=message.adapter_id,
            external_message_id=message.external_message_id,
            internal_message_id=client_turn_id,
            target_agent_id=binding.target_agent_id,
            status="completed",
            result=result,
        )
        return {"status": "completed", "reply_sent": reply_sent, "result": result}

    async def send_adapter_reply(
        self,
        adapter: AccessAdapter,
        *,
        channel_key: str,
        outbound_reply_id: str,
        text: str,
    ) -> bool:
        """Send one adapter reply unless its outbound id was already sent."""
        existing = self._repository.get_idempotency(outbound_reply_id) if self._repository else None
        if existing is not None:
            return False
        await adapter.send(
            AdapterReply(channel_key=channel_key, outbound_reply_id=outbound_reply_id, text=text)
        )
        self._put_idempotency(
            key=outbound_reply_id,
            direction="outbound",
            adapter_id=adapter.adapter_id,
            external_message_id=None,
            internal_message_id=outbound_reply_id,
            target_agent_id=None,
            status="completed",
            result={"text": text},
        )
        return True

    def registered_agents(self) -> list[RegisteredAgent]:
        """Return currently registered fresh runtime routes."""
        now = time.monotonic()
        return [
            RegisteredAgent(
                agent_id=route.agent_id,
                connection_id=route.connection_id,
                pid=route.pid,
                protocol_version=route.protocol_version,
                heartbeat_fresh=True,
                heartbeat_age_seconds=now - route.last_seen,
            )
            for route in self._routes.values()
            if self._route_is_fresh(route, now)
        ]

    def register_resource(
        self,
        service_id: str,
        *,
        capabilities: tuple[str, ...] = (),
        owner: str = "GlobalResourceHost",
        status: str = "healthy",
        connected: bool = True,
        last_error: str | None = None,
    ) -> None:
        """Register or update one resource route in the KernelBus projection."""

        from kernel.kernel_bus.messages import service_kind

        if service_kind(service_id) != "resource":
            raise ValueError("register_resource requires resource:<name> service id")
        current = self._resource_records.get(service_id)
        generation = current.generation + 1 if current is not None else 1
        record_status = (
            status if status in {"healthy", "degraded", "unavailable", "closed"} else "healthy"
        )
        self._resource_records[service_id] = BusServiceRecord(
            serviceId=service_id,
            kind="resource",
            status=record_status,  # type: ignore[arg-type]
            capabilities=capabilities,
            connected=connected,
            generation=generation,
            owner=owner,
            routeReady=connected and status == "healthy",
            lastError=last_error,
        )
        self._topology_revision += 1

    def mark_resource_unavailable(self, service_id: str, error: str | None = None) -> None:
        """Mark a registered resource as unavailable."""

        current = self._resource_records.get(service_id)
        if current is None:
            return
        self._resource_records[service_id] = current.model_copy(
            update={
                "status": "unavailable",
                "connected": False,
                "route_ready": False,
                "last_error": error,
            },
        )
        self._topology_revision += 1

    def bus_topology_snapshot(self) -> BusTopologySnapshot:
        """Return the KernelBus topology projection for Agents and probes."""

        now = time.monotonic()
        services: list[BusServiceRecord] = []
        for route in self._routes.values():
            status = self._route_status(route, now)
            services.append(
                BusServiceRecord(
                    serviceId=f"agent:{route.agent_id}",
                    kind="agent",
                    status="healthy" if status.status == "registered" else "degraded",
                    capabilities=(),
                    connected=status.status == "registered",
                    generation=1,
                    owner="AgentRuntimeHost",
                    routeReady=status.status == "registered",
                    lastError=None if status.status == "registered" else status.status,
                )
            )
        services.extend(self._resource_records.values())
        return BusTopologySnapshot(
            revision=self._topology_revision,
            services=tuple(sorted(services, key=lambda record: record.service_id)),
        )

    def route_status(self, agent_id: str) -> RouteStatus:
        """Return route status without delivering messages."""
        route = self._routes.get(agent_id)
        if route is None:
            return RouteStatus(
                agent_id=agent_id,
                status="unavailable",
                heartbeat_fresh=False,
                stale_timeout_seconds=self._stale_timeout_seconds,
            )
        return self._route_status(route, time.monotonic())

    def evict_stale(self) -> list[str]:
        """Remove stale routes and return evicted agent ids."""
        now = time.monotonic()
        stale = [
            agent_id
            for agent_id, route in self._routes.items()
            if now - route.last_seen > self._stale_timeout_seconds
        ]
        for agent_id in stale:
            self._routes.pop(agent_id, None)
        return stale

    def control_deliver_turn(self, _request: DeliverTurnRequest) -> None:
        """Control-plane guard: Agent Hub control API cannot deliver turns."""
        raise PermissionError("Agent Hub control API cannot deliver turns")

    def close(self) -> None:
        """Close router-owned ResourceStore handles."""
        if self._repository is not None:
            self._repository.close()

    def declare_adapter(
        self,
        *,
        adapter_id: str,
        adapter_type: str,
        config: dict[str, object] | None = None,
        enabled: bool = True,
        actor: str | None = None,
    ) -> int:
        """Declare or update one Access Router-owned adapter."""
        if self._repository is None:
            return 0
        return self._repository.declare_adapter(
            adapter_id=adapter_id,
            adapter_type=adapter_type,
            config=config or {},
            enabled=enabled,
            actor=actor,
        )

    def set_channel_binding(
        self,
        *,
        binding_id: str,
        adapter_id: str,
        channel_key: str,
        target_agent_id: str,
        target_session_id: str | None = None,
        actor: str | None = None,
    ) -> int:
        """Bind an external adapter channel to a target Agent route."""
        if self._repository is None:
            return 0
        return self._repository.set_channel_binding(
            binding_id=binding_id,
            adapter_id=adapter_id,
            channel_key=channel_key,
            target_agent_id=target_agent_id,
            target_session_id=target_session_id,
            actor=actor,
        )

    def _fresh_route(self, agent_id: str) -> _RuntimeConnection:
        route = self._routes.get(agent_id)
        if route is None:
            raise RouteUnavailable(f"route unavailable: {agent_id}")
        if not self._route_is_fresh(route, time.monotonic()):
            raise RouteUnavailable(f"route stale: {agent_id}")
        return route

    def touch_runtime(self, connection_id: str) -> None:
        """Refresh observable transport activity for one runtime connection."""
        self._route_by_connection_id(connection_id).last_seen = time.monotonic()

    def _route_status(self, route: _RuntimeConnection, now: float) -> RouteStatus:
        age = now - route.last_seen
        fresh = self._route_is_fresh(route, now)
        return RouteStatus(
            agent_id=route.agent_id,
            status="registered" if fresh else "stale",
            connection_id=route.connection_id,
            pid=route.pid,
            heartbeat_fresh=fresh,
            heartbeat_age_seconds=age,
            stale_timeout_seconds=self._stale_timeout_seconds,
        )

    def _route_is_fresh(self, route: _RuntimeConnection, now: float) -> bool:
        return now - route.last_seen <= self._stale_timeout_seconds

    def _route_by_connection_id(self, connection_id: str) -> _RuntimeConnection:
        for route in self._routes.values():
            if route.connection_id == connection_id:
                return route
        raise RouteUnavailable(f"connection unavailable: {connection_id}")

    def _record_adapter_status(
        self, adapter_id: str, status: str, error: str | None = None
    ) -> None:
        if self._repository is not None:
            self._repository.record_adapter_status(adapter_id, status, error)

    def _resolve_binding(self, adapter_id: str, channel_key: str) -> ChannelBinding | None:
        return (
            self._repository.resolve_binding(adapter_id, channel_key) if self._repository else None
        )

    def _put_idempotency(
        self,
        *,
        key: str,
        direction: str,
        adapter_id: str | None,
        external_message_id: str | None,
        internal_message_id: str,
        target_agent_id: str | None,
        status: str,
        result: dict[str, object] | None,
    ) -> None:
        if self._repository is not None:
            self._repository.put_idempotency(
                key=key,
                direction=direction,
                adapter_id=adapter_id,
                external_message_id=external_message_id,
                internal_message_id=internal_message_id,
                target_agent_id=target_agent_id,
                status=status,
                result=result,
            )


def _reply_text(result: dict[str, object]) -> str:
    for key in ("text", "reply"):
        value = result.get(key)
        if isinstance(value, str):
            return value
    return ""


def _normalize_turn_handler(handler: RuntimeHandlerInput) -> RuntimeHandler:
    if len(inspect.signature(handler).parameters) >= 2:
        return handler

    async def _wrapped(
        request: DeliverTurnRequest | RuntimeAcpRequest,
        _client_request_proxy: ClientRequestProxy | None,
    ) -> dict[str, object]:
        return await handler(request)

    return _wrapped


def _normalize_acp_handler(handler: RuntimeAcpHandlerInput) -> RuntimeAcpHandler:
    if len(inspect.signature(handler).parameters) >= 2:
        return handler

    async def _wrapped(
        request: RuntimeAcpRequest,
        _client_request_proxy: ClientRequestProxy | None,
    ) -> dict[str, object]:
        return await handler(request)

    return _wrapped
