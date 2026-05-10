"""Resource revision tracking for Agent Hub."""

from __future__ import annotations

from dataclasses import dataclass

from kernel.agent_hub.contracts import CallerIdentity, CallerIdentityKind, ManagementCapability


@dataclass(frozen=True)
class ResourceChangedEvent:
    """Revision bump emitted after a global resource write."""

    resource_key: str
    revision: int
    agent_id: str


class ResourceRevisionTracker:
    """Coordinates resource revision writes and snapshots."""

    def __init__(self) -> None:
        self._revisions: dict[str, int] = {
            "config.global": 0,
            "agent_definitions": 0,
            "skills.global": 0,
            "memory.global": 0,
            "mcp.global": 0,
            "hooks.global": 0,
            "tool_policy.global": 0,
        }
        self._events: list[ResourceChangedEvent] = []

    def current_revisions(self) -> dict[str, int]:
        """Return a copy of the current resource revision map."""

        return dict(self._revisions)

    def events(self) -> tuple[ResourceChangedEvent, ...]:
        """Return emitted events for tests/probes."""

        return tuple(self._events)

    def write(
        self,
        resource_key: str,
        *,
        caller: CallerIdentity,
        capability: ManagementCapability,
        expected_revision: int | None = None,
    ) -> ResourceChangedEvent:
        """Validate and bump one global resource revision."""

        if caller.kind is not CallerIdentityKind.DURABLE_AGENT or caller.agent_id is None:
            raise PermissionError("resource revision write requires durable agent identity")
        if capability is not ManagementCapability.GLOBAL_RESOURCE_WRITE:
            raise PermissionError("resource revision write capability required")

        current = self._revisions.get(resource_key, 0)
        if expected_revision is not None and expected_revision != current:
            raise ValueError(
                f"revision mismatch for {resource_key}: expected "
                f"{expected_revision}, current {current}"
            )

        next_revision = current + 1
        self._revisions[resource_key] = next_revision
        event = ResourceChangedEvent(
            resource_key=resource_key,
            revision=next_revision,
            agent_id=caller.agent_id,
        )
        self._events.append(event)
        return event
