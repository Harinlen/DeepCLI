"""User-facing gateway management facade over Access Router metadata."""

from __future__ import annotations

import builtins
from dataclasses import dataclass

from kernel.access_router.repository import AccessRouterRepository


@dataclass(frozen=True, slots=True)
class GatewayReloadResult:
    gateway_id: str
    status: str
    error: str | None = None


class GatewayCommandService:
    """Implements `/gateways` command semantics on Access Router tables."""

    def __init__(self, repository: AccessRouterRepository) -> None:
        self._repo = repository

    def list(self) -> list[dict[str, object]]:
        return self._repo.list_adapters()

    def create(
        self,
        *,
        gateway_id: str,
        gateway_type: str,
        config: dict[str, object] | None = None,
        enabled: bool = True,
        actor: str = "primary",
    ) -> dict[str, object]:
        if self._repo.get_adapter(gateway_id) is not None:
            raise ValueError(f"gateway already exists: {gateway_id}")
        revision = self._repo.declare_adapter(
            adapter_id=gateway_id,
            adapter_type=gateway_type,
            config=config or {},
            enabled=enabled,
            actor=actor,
        )
        row = self._repo.get_adapter(gateway_id)
        if row is None:
            raise RuntimeError(f"gateway create did not persist: {gateway_id}")
        return {**row, "revision": revision}

    def delete(
        self,
        gateway_id: str,
        *,
        confirm: bool = False,
        actor: str = "primary",
    ) -> dict[str, object]:
        if not confirm:
            raise PermissionError("delete requires --confirm")
        result = self._repo.remove_adapter(gateway_id, actor=actor)
        return {
            "gateway_id": gateway_id,
            "deleted": True,
            "revision": result["revision"],
            "disabled_bindings": result["disabled_bindings"],
        }

    def status(self, gateway_id: str | None = None) -> builtins.list[dict[str, object]]:
        rows = self._repo.list_adapters()
        if gateway_id is not None:
            rows = [row for row in rows if row["gateway_id"] == gateway_id]
            if not rows:
                raise KeyError(f"unknown gateway: {gateway_id}")
        return rows

    def enable(self, gateway_id: str, *, actor: str = "primary") -> int:
        return self._repo.set_adapter_enabled(gateway_id, True, actor=actor)

    def disable(self, gateway_id: str, *, actor: str = "primary") -> int:
        return self._repo.set_adapter_enabled(gateway_id, False, actor=actor)

    def reload(self, gateway_id: str, *, fail: bool = False) -> GatewayReloadResult:
        if self._repo.get_adapter(gateway_id) is None:
            raise KeyError(f"unknown gateway: {gateway_id}")
        if fail:
            self._repo.record_adapter_status(gateway_id, "failed", "test gateway reload failed")
            return GatewayReloadResult(
                gateway_id=gateway_id,
                status="failed",
                error="test gateway reload failed",
            )
        self._repo.record_adapter_status(gateway_id, "reloaded")
        return GatewayReloadResult(gateway_id=gateway_id, status="reloaded")

    def bindings(
        self,
        *,
        gateway_id: str | None = None,
        agent_id: str | None = None,
    ) -> builtins.list[dict[str, object]]:
        return self._repo.list_channel_bindings(
            adapter_id=gateway_id,
            target_agent_id=agent_id,
        )

    def bind(
        self,
        *,
        gateway_id: str,
        channel_key: str,
        agent_id: str,
        session_id: str | None = None,
        actor: str = "primary",
    ) -> dict[str, object]:
        gateway = self._repo.get_adapter(gateway_id)
        if gateway is None:
            raise KeyError(f"unknown gateway: {gateway_id}")
        if not bool(gateway["enabled"]):
            raise PermissionError(f"gateway disabled: {gateway_id}")
        existing = self._repo.resolve_binding(gateway_id, channel_key)
        if existing is not None:
            raise ValueError(f"gateway channel already bound: {gateway_id}:{channel_key}")
        binding_id = f"{gateway_id}:{channel_key}"
        revision = self._repo.set_channel_binding(
            binding_id=binding_id,
            adapter_id=gateway_id,
            channel_key=channel_key,
            target_agent_id=agent_id,
            target_session_id=session_id,
            actor=actor,
        )
        return {
            "binding_id": binding_id,
            "gateway_id": gateway_id,
            "channel_key": channel_key,
            "target_agent_id": agent_id,
            "target_session_id": session_id,
            "revision": revision,
        }

    def unbind(self, binding_id: str, *, actor: str = "primary") -> None:
        self._repo.delete_channel_binding(binding_id, actor=actor)
