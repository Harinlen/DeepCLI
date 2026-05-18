"""Agent Hub control-plane view of Access Router state."""

from __future__ import annotations

from kernel.access_router.router import AccessRouter
from kernel.access_router.schemas import (
    RegisteredAgent,
    RouteStatus,
    RouterHealth,
    RoutingSnapshotStatus,
)


class AccessRouterControlAPI:
    """Read-only/control methods exposed to Agent Hub.

    This API intentionally has no turn-delivery method. User messages stay on
    the Access Router -> Agent Runtime hot path.
    """

    def __init__(self, router: AccessRouter) -> None:
        self._router = router

    def health(self) -> RouterHealth:
        agents = self._router.registered_agents()
        return RouterHealth(
            ready=bool(agents),
            registered_agents=len(agents),
            agent_hub_forward_count=self._router.agent_hub_forward_count,
        )

    def registered_agents(self) -> list[RegisteredAgent]:
        return self._router.registered_agents()

    def route_status(self, agent_id: str) -> RouteStatus:
        return self._router.route_status(agent_id)

    def reload_routing_snapshot(self, expected_revision: int | None = None) -> RoutingSnapshotStatus:
        return RoutingSnapshotStatus(reloaded=True, revision=expected_revision)
