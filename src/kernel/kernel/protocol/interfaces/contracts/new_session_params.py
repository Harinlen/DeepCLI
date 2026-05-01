"""Parameters for creating a new session."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class NewSessionParams(BaseModel):
    """Input to :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.new`."""

    cwd: str
    """Absolute path of the working directory for this session."""

    mcp_servers: list[dict] = []
    """MCP server connection specs.  Passed through to the MCP
    subsystem as-is; the session layer owns the parsing."""

    meta: dict[str, Any] | None = None
    """ACP ``_meta`` extension.  Used for worktree startup mode
    (``meta.worktree.slug``) and future protocol extensions."""
