"""Tool semantic categories used by Orchestrator and ToolManager."""

from __future__ import annotations

from enum import Enum


class ToolKind(str, Enum):
    """Semantic category of a tool.

    The enum is intentionally broader than ACP's tool kinds.  The Mustang kernel uses it
    for scheduling and UI hints, then maps unknown ACP values to ``other`` at
    the protocol boundary.
    """

    # Read-only classes may be batched when each tool also opts into concurrency.
    read = "read"
    search = "search"
    fetch = "fetch"
    think = "think"
    # DeepCLI-only: tools that spawn agents or route work across sessions.
    orchestrate = "orchestrate"
    # Mutating classes stay serial unless a future tool proves stronger safety.
    edit = "edit"
    delete = "delete"
    move = "move"
    execute = "execute"
    other = "other"

    @property
    def is_read_only(self) -> bool:
        """True for tool kinds that are safe to execute concurrently.

        Returns:
            ``True`` for read/search/fetch/think tool categories.
        """
        return self in {
            ToolKind.read,
            ToolKind.search,
            ToolKind.fetch,
            ToolKind.think,
        }
