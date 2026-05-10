"""Result of processing a prompt turn."""

from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel

StopReason = Literal[
    "end_turn",
    "max_tokens",
    "max_turn_requests",
    "refusal",
    "cancelled",
    "error",
]
"""Why the agent stopped the current turn.

``end_turn``
    The LLM finished normally without requesting more tools.
``max_tokens``
    Token limit reached.
``max_turn_requests``
    Maximum number of LLM round-trips in a single turn exceeded.
``refusal``
    The agent refused to continue.
``cancelled``
    ``session/cancel`` was received.  MUST be returned as a success
    response, NOT as a JSON-RPC error — clients distinguish this from
    actual failures by checking ``stopReason``.
``error``
    The turn ended because the agent/provider failed after any internal
    recovery attempts.  The error is surfaced through streamed content
    before the prompt response closes.
"""


class PromptResult(BaseModel):
    """Output from :meth:`~kernel.core.protocol.interfaces.session_handler.SessionHandler.prompt`."""

    stop_reason: StopReason
    meta: dict[str, Any] | None = None
