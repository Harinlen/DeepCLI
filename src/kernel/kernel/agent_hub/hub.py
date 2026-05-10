"""Agent Hub skeleton composed from Manager, Router, and ResourceRevisionTracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from kernel.agent_hub.manager import AgentHubManager
from kernel.agent_hub.resource_revisions import ResourceRevisionTracker
from kernel.agent_hub.router import AgentHubRouter


@dataclass
class AgentHub:
    """No-FastAPI Agent Hub composition for Batch B."""

    router: AgentHubRouter = field(default_factory=AgentHubRouter)
    manager: AgentHubManager = field(default_factory=AgentHubManager)
    resource_revisions: ResourceRevisionTracker = field(
        default_factory=ResourceRevisionTracker
    )
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def readiness(self) -> dict[str, object]:
        """Return a local readiness snapshot."""

        return {
            "ready": True,
            "startedAt": self.started_at,
            "definedAgents": len(self.manager.list_definitions()),
            "registeredAgents": len(self.manager.list_runtime_records()),
            "primaryRegistered": self.manager.get_runtime_record("primary") is not None,
            "schemaVersion": "agent-hub.b",
        }
