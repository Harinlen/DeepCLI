"""Parameters for setting a session configuration option."""

from __future__ import annotations

from pydantic import BaseModel


class SetConfigOptionParams(BaseModel):
    """Input to :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.set_config_option`."""

    session_id: str
    config_id: str
    """ID of the configuration option to change."""
    value: str
    """ID of the new value for the option."""
