"""Parameters for loading (resuming) an existing session."""

from __future__ import annotations

from pydantic import BaseModel


class LoadSessionParams(BaseModel):
    """Input to :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.load`."""

    session_id: str
    cwd: str
    """Working directory; MUST be an absolute path."""
    mcp_servers: list[dict] = []
