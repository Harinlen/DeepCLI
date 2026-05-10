"""Agent-scoped resource revision view."""

from __future__ import annotations

from collections.abc import Callable


class AgentResourceView:
    """Caches global resource revisions for one Agent Runtime."""

    def __init__(
        self,
        current_revisions: Callable[[], dict[str, int]],
        reload_changed: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        self._current_revisions = current_revisions
        self._reload_changed = reload_changed
        self._seen: dict[str, int] = {}

    @property
    def seen_revisions(self) -> dict[str, int]:
        """Return a copy of revisions observed by this runtime."""

        return dict(self._seen)

    async def check_and_refresh_before_turn(self) -> dict[str, int]:
        """Refresh changed global resource views at turn start."""

        current = self._current_revisions()
        changed = {
            key: revision
            for key, revision in current.items()
            if self._seen.get(key) != revision
        }
        if changed:
            if self._reload_changed is not None:
                self._reload_changed(changed)
            self._seen.update(changed)
        return changed


class NullAgentResourceView:
    """Compatibility view used before Agent Hub is wired into SessionManager."""

    async def check_and_refresh_before_turn(self) -> dict[str, int]:
        """No-op compatibility hook."""

        return {}
