"""Result of a session mode switch."""

from __future__ import annotations

from pydantic import BaseModel


class SetModeResult(BaseModel):
    """Output from :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.set_mode`.

    ACP ``session/set_mode`` returns an empty object on success.
    """
