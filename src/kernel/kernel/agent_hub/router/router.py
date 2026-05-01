"""Agent Hub Router message-plane primitives."""

from __future__ import annotations

from dataclasses import dataclass

from kernel.agents import RouterFrame, RoutingSnapshot


@dataclass(frozen=True)
class RoutedRouterFrame:
    """A RouterFrame after snapshot-only target resolution."""

    target_agent_id: str
    frame: RouterFrame


class AgentHubRouter:
    """Read-only message-plane target resolver.

    Router owns no Manager reference.  Manager publishes snapshots; Router
    uses the latest local copy while routing frames.
    """

    def __init__(self) -> None:
        self._snapshot = RoutingSnapshot(revision=0)

    @property
    def snapshot(self) -> RoutingSnapshot:
        """Current read-only routing snapshot."""

        return self._snapshot

    def update_snapshot(self, snapshot: RoutingSnapshot) -> None:
        """Replace the local routing snapshot."""

        self._snapshot = snapshot

    def _known_agent_ids(self) -> set[str]:
        """Return durable agents present in the latest routing snapshot."""

        return {entry.agent_id for entry in self._snapshot.entries}

    def resolve_target(self, frame: RouterFrame) -> str | None:
        """Resolve a RouterFrame target using only the local snapshot.

        Explicit targets are accepted only when they name a durable Agent in
        the snapshot.  Ephemeral AgentTool children never appear there, so
        they cannot become Router targets.
        """

        known_agents = self._known_agent_ids()
        if frame.target.agent_id is not None:
            if frame.target.agent_id in known_agents:
                return frame.target.agent_id
            return None

        for entry in self._snapshot.entries:
            if entry.native_default:
                return entry.agent_id
        return None

    def route_message(self, frame: RouterFrame) -> RoutedRouterFrame | None:
        """Resolve and package a message-plane frame for delivery."""

        target_agent_id = self.resolve_target(frame)
        if target_agent_id is None:
            return None
        return RoutedRouterFrame(target_agent_id=target_agent_id, frame=frame)
