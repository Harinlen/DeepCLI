"""Result of loading an existing session."""

from __future__ import annotations

from pydantic import BaseModel, Field

from kernel.protocol.interfaces.contracts.session_config import (
    ConfigOptionDescriptor,
    SessionModeState,
)


class LoadSessionResult(BaseModel):
    """Output from :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.load`.

    ACP ``session/load`` returns ``null`` on the wire (the history is
    delivered via ``session/update`` notifications before this
    response).  We model that as an empty result object so handlers
    have a consistent return-type contract.
    """

    config_options: list[ConfigOptionDescriptor] = Field(default_factory=list)
    """Current ACP session config option descriptors after replay."""

    modes: SessionModeState | None = None
    """Current ACP mode state after replay."""
