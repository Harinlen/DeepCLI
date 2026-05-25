"""Agent-scoped runtime context for SessionManager.

Batch B1 keeps the existing single-primary behavior as the default while
making the session store path injectable for future durable Agent runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentContext:
    """Runtime context owned by one durable Agent."""

    agent_id: str
    workspace: Path
    state_dir: Path
    session_store_path: Path
    name: str | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    model_profile: str | None = None
    prompt_profile: str | None = None
    tool_policy: str | None = None
    memory_scopes: tuple[str, ...] = ("global", "workspace", "agent")
    skill_scopes: tuple[str, ...] = ("builtin", "global", "workspace", "agent")
    mcp_scopes: tuple[str, ...] = ("global", "agent")
    hook_profile: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def primary_compat(cls, *, state_dir: Path, workspace: Path | None = None) -> "AgentContext":
        """Return the current single-primary layout used before Agent Hub."""

        sessions_dir = state_dir.parent / "sessions"
        return cls(
            agent_id="primary",
            name="Primary",
            workspace=workspace or Path.cwd(),
            state_dir=state_dir,
            session_store_path=sessions_dir / "sessions.db",
            metadata={"compat": "single-primary"},
        )

    @property
    def sessions_dir(self) -> Path:
        """Directory passed to SessionStore."""

        return self.session_store_path.parent
