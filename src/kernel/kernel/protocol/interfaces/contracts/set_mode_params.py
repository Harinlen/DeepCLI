"""Parameters for switching session mode."""

from __future__ import annotations

from pydantic import BaseModel


class SetModeParams(BaseModel):
    """Input to :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.set_mode`."""

    session_id: str
    mode_id: str
    """ID of the mode to activate (must be one advertised by the agent)."""
