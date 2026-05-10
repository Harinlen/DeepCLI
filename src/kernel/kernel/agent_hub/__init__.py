"""Agent Hub skeleton: Router, Manager, and resource revision tracking."""

from typing import Any

__all__ = [
    "AgentDefinitionsConfig",
    "AgentHub",
    "AgentHubManager",
    "AgentHubRouter",
    "AgentHubWebSocketServer",
    "ResourceRevisionTracker",
    "request_hub",
]


def __getattr__(name: str) -> Any:
    """Load app/server dependencies only when callers request them."""

    if name == "AgentHub":
        from kernel.agent_hub.hub import AgentHub

        return AgentHub
    if name == "AgentHubWebSocketServer" or name == "request_hub":
        from kernel.agent_hub.server import AgentHubWebSocketServer, request_hub

        return {
            "AgentHubWebSocketServer": AgentHubWebSocketServer,
            "request_hub": request_hub,
        }[name]
    if name == "AgentDefinitionsConfig" or name == "AgentHubManager":
        from kernel.agent_hub.manager import AgentDefinitionsConfig, AgentHubManager

        return {
            "AgentDefinitionsConfig": AgentDefinitionsConfig,
            "AgentHubManager": AgentHubManager,
        }[name]
    if name == "AgentHubRouter":
        from kernel.agent_hub.router import AgentHubRouter

        return AgentHubRouter
    if name == "ResourceRevisionTracker":
        from kernel.agent_hub.resource_revisions import ResourceRevisionTracker

        return ResourceRevisionTracker
    raise AttributeError(name)
