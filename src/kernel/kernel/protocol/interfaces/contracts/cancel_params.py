"""Parameters for the session/cancel notification."""

from __future__ import annotations

from pydantic import BaseModel


class CancelParams(BaseModel):
    """Input to :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.cancel`.

    ``session/cancel`` is a notification (no response), so the handler
    returns ``None``.
    """

    session_id: str
    """The session whose in-flight prompt turn should be cancelled."""
