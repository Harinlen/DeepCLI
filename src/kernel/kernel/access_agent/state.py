"""Access Agent readiness state.

The current product process still hosts the compatibility `/session` route,
but this state object is the Batch C boundary for the future standalone Access
Agent process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import kernel


@dataclass
class AccessAgentState:
    """Readiness flags reported to native clients and probes."""

    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    process_ready: bool = True
    hub_ready: bool = False
    primary_registered: bool = False
    default_route_ready: bool = False
    platform_bindings_active: bool = False
    hub_endpoint: str | None = None

    def mark_hub_ready(self) -> None:
        """Record that Agent Hub is reachable."""

        self.hub_ready = True

    def mark_primary_registered(self) -> None:
        """Record that the Primary Agent has registered with the Hub."""

        self.primary_registered = True
        self.default_route_ready = True

    def metadata(self) -> dict[str, object]:
        """Return stable metadata for CLI/Probe/Web UI."""

        return {
            "name": "mustang-access-agent",
            "version": kernel.__version__,
            "startedAt": self.started_at,
        }

    def readiness(self) -> dict[str, object]:
        """Return detailed startup/readiness flags."""

        return {
            "process_ready": self.process_ready,
            "hub_ready": self.hub_ready,
            "primary_registered": self.primary_registered,
            "default_route_ready": self.default_route_ready,
            "platform_bindings_active": self.platform_bindings_active,
        }

    async def refresh_from_hub(self) -> None:
        """Refresh Hub/Primary readiness through internal Hub WebSocket."""

        if self.hub_endpoint is None:
            return
        from kernel.agent_hub.server import request_hub
        from kernel.agents import HubFrame, HubFrameType

        response = await request_hub(
            self.hub_endpoint,
            HubFrame(
                frame_id="access-readiness",
                frame_type=HubFrameType.REQUEST,
                contract="hub.readiness",
            ),
        )
        if response.payload.get("ok") is not True:
            return
        readiness = response.payload.get("readiness", {})
        if not isinstance(readiness, dict):
            return
        self.hub_ready = bool(readiness.get("ready"))
        self.primary_registered = bool(readiness.get("primaryRegistered"))
        self.default_route_ready = self.hub_ready and self.primary_registered

    def startup_error(self) -> dict[str, str] | None:
        """Return the explicit prompt-blocking reason before default route is ready."""

        if not self.hub_ready:
            return {
                "code": "kernel_starting",
                "message": "Agent Hub is not ready yet.",
            }
        if not self.primary_registered:
            return {
                "code": "primary_agent_starting",
                "message": "Primary Agent is not registered yet.",
            }
        if not self.default_route_ready:
            return {
                "code": "default_route_starting",
                "message": "Default route is not ready yet.",
            }
        return None
