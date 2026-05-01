"""Result for official ACP ``session/resume``."""

from __future__ import annotations

from pydantic import Field

from kernel.protocol.interfaces.contracts.load_session_result import LoadSessionResult


class ResumeSessionResult(LoadSessionResult):
    """Current runtime view after reattachment without replay."""

    replayed: bool = Field(default=False)
