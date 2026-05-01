"""Result of creating a new session."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kernel.protocol.interfaces.contracts.session_config import (
    ConfigOptionDescriptor,
    SessionModeState,
)


class NewSessionResult(BaseModel):
    """Output from :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.new`."""

    session_id: str
    """Unique identifier for the created session."""

    config_options: list[ConfigOptionDescriptor] = Field(default_factory=list)
    """Initial ACP session config option descriptors."""

    modes: SessionModeState | None = None
    """Initial ACP mode state."""
