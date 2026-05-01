"""Forward tool permission requests to the connected client.

The orchestrator pauses each tool call awaiting a ``PermissionResponse``;
this mixin pushes the request out via ACP, awaits the user's choice, and
records both request and response in the session event log.  Sessions
with no live client default to ``reject`` — silent sessions cannot grant
permissions.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from kernel.orchestrator.types import PermissionRequest, PermissionResponse
from kernel.protocol.acp.schemas.permission import (
    PermissionOption,
    PermissionOutcomeSelected,
    RequestPermissionRequest,
    RequestPermissionResponse,
    ToolCallUpdate,
)
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.events import PermissionRequestEvent, PermissionResponseEvent
from kernel.session.runtime.state import Session

logger = logging.getLogger("kernel.session")


class SessionPermissionMixin(_SessionMixinBase):
    """Routes orchestrator permission requests through the connected client."""

    async def _on_permission(self, session: Session, req: PermissionRequest) -> PermissionResponse:
        """Forward one permission request to the connected client.

        Persists both the request and the response to the session log so
        a future replay shows the same decisions.

        Args:
            session: Session whose tool call needs approval.
            req: Permission request from the orchestrator (tool name,
                summary, risk level, full input).

        Returns:
            ``PermissionResponse`` with ``decision`` of ``allow_once``,
            ``allow_always``, or ``reject``.  Falls back to ``reject``
            when no client is connected or the round-trip raises —
            failing closed is the safer default.
        """
        await self._write_event(
            session,
            PermissionRequestEvent,
            tool_call_id=req.tool_use_id,
            tool_name=req.tool_name,
            input_summary=req.input_summary,
            risk_level=req.risk_level,
        )

        if not session.senders:
            logger.warning(
                "session=%s: no connected client for permission request — rejecting",
                session.session_id,
            )
            return PermissionResponse(decision="reject")

        sender = next(iter(session.senders.values()))
        acp_params = RequestPermissionRequest(
            session_id=session.session_id,
            tool_call=ToolCallUpdate(
                tool_call_id=req.tool_use_id,
                title=req.tool_title,
                input_summary=req.input_summary,
            ),
            options=_acp_permission_options(req),
            tool_input=req.tool_input,
        )

        decision: Literal["allow_once", "allow_always", "reject"] = "reject"
        updated_input: dict[str, Any] | None = None
        try:
            response = await sender.request(
                "session/request_permission",
                acp_params,
                result_type=RequestPermissionResponse,
            )
        except Exception:
            logger.exception(
                "session=%s: permission request failed — rejecting", session.session_id
            )
            decision = "reject"
        else:
            # The outcome is a discriminated union: a "selected" branch
            # carries the chosen option id, and a "cancelled" branch falls
            # through to reject — the orchestrator treats both as denial.
            if isinstance(response.outcome, PermissionOutcomeSelected):
                option_id = response.outcome.option_id
                if option_id == "allow_once":
                    decision = "allow_once"
                elif option_id == "allow_always":
                    decision = "allow_always"
                else:
                    decision = "reject"
                updated_input = response.outcome.updated_input
            else:
                decision = "reject"
                updated_input = None

        await self._write_event(
            session,
            PermissionResponseEvent,
            tool_call_id=req.tool_use_id,
            decision=decision,
        )
        return PermissionResponse(decision=decision, updated_input=updated_input)


def _acp_permission_options(req: PermissionRequest) -> list[PermissionOption]:
    """Map orchestrator permission options to ACP options."""
    if req.options:
        return [
            PermissionOption(option_id=option.option_id, name=option.name, kind=option.kind)
            for option in req.options
        ]
    return [
        PermissionOption(option_id="allow_once", name="Allow once", kind="allow_once"),
        PermissionOption(option_id="allow_always", name="Allow always", kind="allow_always"),
        PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
    ]
