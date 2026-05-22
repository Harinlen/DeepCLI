"""User-facing durable Agent command facade."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

from kernel.access_router.repository import AccessRouterRepository
from kernel.agent_hub.manager.manager import AgentManager
from kernel.agent_hub.manager.schemas import (
    CreateAgentSpec,
    GrantCapability,
    ResourceScope,
)
from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import DeliverTurnRequest


class AgentCommandService:
    """Implements `/agents` and `/agent send` command semantics."""

    def __init__(
        self,
        *,
        manager: AgentManager,
        gateway_repository: AccessRouterRepository | None = None,
        router: AccessRouter | None = None,
    ) -> None:
        self._manager = manager
        self._gateways = gateway_repository
        self._router = router

    def list(self, *, include_bindings: bool = False) -> dict[str, Any]:
        rows: dict[str, Any] = {"agents": [row.model_dump() for row in self._manager.list()]}
        if include_bindings:
            rows["bindings"] = self.bindings()
        return rows

    def add(
        self,
        agent_id: str,
        *,
        workspace: Path,
        name: str | None = None,
        state_dir: Path | None = None,
        actor_agent_id: str = "primary",
    ) -> dict[str, object]:
        record = self._manager.create(
            CreateAgentSpec(
                agent_id=agent_id,
                name=name or agent_id,
                workspace=workspace,
                state_dir=state_dir or self._manager.home / "agents" / agent_id,
            ),
            actor_agent_id=actor_agent_id,
        )
        return record.model_dump()

    def delete(
        self,
        agent_id: str,
        *,
        confirm: bool,
        actor_agent_id: str = "primary",
    ) -> dict[str, object]:
        record = self._require_agent(agent_id)
        result = self._manager.delete(
            agent_id,
            expected_revision=record.revision,
            actor_agent_id=actor_agent_id,
            confirm=confirm,
        )
        return result.model_dump()

    def set_identity(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        avatar: str | None = None,
        theme: str | None = None,
        identity_patch: dict[str, object] | None = None,
        actor_agent_id: str = "primary",
    ) -> dict[str, object]:
        record = self._require_agent(agent_id)
        updated = self._manager.set_identity(
            agent_id,
            expected_revision=record.revision,
            actor_agent_id=actor_agent_id,
            name=name,
            avatar=avatar,
            theme=theme,
            identity_patch=identity_patch,
        )
        return updated.model_dump()

    def bindings(self, *, agent_id: str | None = None) -> builtins.list[dict[str, object]]:
        if self._gateways is None:
            return []
        return self._gateways.list_channel_bindings(target_agent_id=agent_id)

    def bind(
        self,
        *,
        agent_id: str,
        bind: str,
        session_id: str | None = None,
        actor_agent_id: str = "primary",
    ) -> dict[str, object]:
        self._require_agent(agent_id)
        gateway_id, channel_key = _parse_gateway_channel(bind)
        if self._gateways is None:
            raise RuntimeError("gateway repository is unavailable")
        from kernel.access_router.gateway_commands import GatewayCommandService

        return GatewayCommandService(self._gateways).bind(
            gateway_id=gateway_id,
            channel_key=channel_key,
            agent_id=agent_id,
            session_id=session_id,
            actor=actor_agent_id,
        )

    def unbind(
        self,
        *,
        agent_id: str,
        bind: str | None = None,
        all: bool = False,
        actor_agent_id: str = "primary",
    ) -> int:
        if self._gateways is None:
            raise RuntimeError("gateway repository is unavailable")
        if all:
            return self._gateways.delete_bindings_for_agent_channel(
                target_agent_id=agent_id,
                actor=actor_agent_id,
            )
        if bind is None:
            raise ValueError("unbind requires --bind or --all")
        gateway_id, channel_key = _parse_gateway_channel(bind)
        return self._gateways.delete_bindings_for_agent_channel(
            target_agent_id=agent_id,
            adapter_id=gateway_id,
            channel_key=channel_key,
            actor=actor_agent_id,
        )

    def health(self, agent_id: str) -> dict[str, object]:
        return self._manager.health(agent_id).model_dump()

    def start(self, agent_id: str, *, router_endpoint: str, router_token: str) -> dict[str, object]:
        return self._manager.start(
            agent_id,
            actor_agent_id="primary",
            router_endpoint=router_endpoint,
            router_token=router_token,
        ).model_dump()

    def stop(self, agent_id: str) -> dict[str, object]:
        return self._manager.stop(agent_id, actor_agent_id="primary").model_dump()

    def restart(
        self, agent_id: str, *, router_endpoint: str, router_token: str
    ) -> dict[str, object]:
        self.stop(agent_id)
        return self.start(agent_id, router_endpoint=router_endpoint, router_token=router_token)

    def grants(self, agent_id: str | None = None) -> builtins.list[dict[str, object]]:
        return [grant.model_dump() for grant in self._manager.list_grants(agent_id)]

    def grant(
        self,
        agent_id: str,
        capability: str,
        *,
        scope: str = "global",
        resource: str | None = None,
        workspace: str | None = None,
        expires_at: str | None = None,
        actor_agent_id: str = "primary",
    ) -> dict[str, object]:
        grant = self._manager.grant(
            agent_id,
            GrantCapability(capability),
            ResourceScope(scope),
            resource_id=resource,
            workspace=workspace,
            granted_by_agent_id=actor_agent_id,
            expires_at=expires_at,
        )
        return grant.model_dump()

    def revoke_grant(self, grant_id: str, *, actor_agent_id: str = "primary") -> dict[str, object]:
        return self._manager.revoke_grant(grant_id, actor_agent_id=actor_agent_id).model_dump()

    async def send(
        self,
        *,
        agent_id: str,
        message: str,
        session_id: str | None = None,
        deliver: bool = True,
    ) -> dict[str, object]:
        if not deliver:
            return {"queued": False, "delivered": False}
        if self._router is None:
            raise RuntimeError("Access Router is unavailable")
        return await self._router.deliver_turn(
            DeliverTurnRequest(
                agent_id=agent_id,
                session_id=session_id or f"agent-send:{agent_id}",
                client_turn_id=f"agent-send:{agent_id}",
                prompt=message,
                idempotency_key=f"agent-send:{agent_id}:{hash(message)}",
            )
        )

    def _require_agent(self, agent_id: str) -> Any:
        record = self._manager.get(agent_id)
        if record is None or record.deleted_at is not None:
            raise KeyError(f"unknown agent: {agent_id}")
        return record


def _parse_gateway_channel(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise ValueError("binding must use <gateway>:<channel>")
    gateway_id, channel_key = value.split(":", 1)
    if not gateway_id or not channel_key:
        raise ValueError("binding must use <gateway>:<channel>")
    return gateway_id, channel_key
