"""Tool authorization and user permission round-trip handling."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from kernel.llm.types import ToolUseContent
from kernel.orchestrator.permissions import (
    PermissionCallback,
    PermissionRequest,
    PermissionResponse,
)
from kernel.orchestrator.tools_exec.permissions import permission_options_from_suggestions

if TYPE_CHECKING:
    from kernel.tool_authz import AuthorizeContext, PermissionDecision, ToolAuthorizer
    from kernel.tools import Tool

logger = logging.getLogger(__name__)

RiskLevel = Literal["low", "medium", "high"]


class ToolAuthorizationMixin:
    """Run ToolAuthorizer and serialize interactive permission prompts."""

    _allow_all_warned: bool
    _permission_lock: asyncio.Lock

    async def _authorize(
        self,
        *,
        authorizer: ToolAuthorizer | None,
        tool: Tool,
        tool_input: dict[str, Any],
        auth_ctx: AuthorizeContext,
        tc: ToolUseContent,
        on_permission: PermissionCallback,
    ) -> PermissionDecision | None:
        """Run authorize plus optional ``on_permission`` round-trip.

        Args:
            authorizer: ToolAuthorizer subsystem, or ``None`` in degraded tests.
            tool: Tool being called.
            tool_input: Effective tool input.
            auth_ctx: Authorization context for the call.
            tc: Original LLM tool-use block.
            on_permission: Interactive permission callback.

        Returns:
            Permission decision, or ``None`` when authorization failed closed.
        """
        if authorizer is None:
            if not self._allow_all_warned:
                logger.warning(
                    "ToolExecutor: ToolAuthorizer unavailable - allowing all tool calls (degraded)"
                )
                self._allow_all_warned = True
            from kernel.tool_authz import PermissionAllow
            from kernel.tool_authz.types import ReasonFailClosed

            return PermissionAllow(
                decision_reason=ReasonFailClosed(error_class="authorizer_unavailable"),
            )

        try:
            decision = await authorizer.authorize(tool=tool, tool_input=tool_input, ctx=auth_ctx)
        except Exception:
            logger.exception("ToolAuthorizer.authorize raised - treating as deny")
            return None

        from kernel.tool_authz import PermissionAsk

        if not isinstance(decision, PermissionAsk):
            return decision

        req = PermissionRequest(
            tool_use_id=tc.id,
            tool_name=tool.name,
            tool_title=tool.user_facing_name(tool_input),
            input_summary=decision.message,
            risk_level=_risk_from_decision(decision),
            tool_input=dict(tool_input),
            options=permission_options_from_suggestions(decision.suggestions),
        )
        try:
            async with self._permission_lock:
                response: PermissionResponse = await on_permission(req)
        except Exception:
            logger.exception("on_permission raised - treating as reject")
            from kernel.tool_authz import PermissionDeny
            from kernel.tool_authz.types import ReasonNoPrompt

            return PermissionDeny(
                message="no interactive channel available",
                decision_reason=ReasonNoPrompt(),
            )

        if response.decision == "reject":
            from kernel.tool_authz import PermissionDeny
            from kernel.tool_authz.types import ReasonFailClosed

            return PermissionDeny(
                message="user rejected permission request",
                decision_reason=ReasonFailClosed(error_class="user_reject"),
            )

        if response.decision == "allow_always":
            try:
                authorizer.grant(tool=tool, tool_input=tool_input, ctx=auth_ctx)
            except Exception:
                logger.exception("authorizer.grant failed - allowing this call anyway")

        from kernel.tool_authz import PermissionAllow
        from kernel.tool_authz.types import ReasonFailClosed

        return PermissionAllow(
            decision_reason=ReasonFailClosed(error_class="user_allow"),
            updated_input=response.updated_input,
        )


def _risk_from_decision(decision: object) -> RiskLevel:
    """Extract a UI risk level from an authorizer decision.

    Args:
        decision: Permission decision object with an optional decision reason.

    Returns:
        ``"low"``, ``"medium"``, or ``"high"``; defaults to ``"medium"``.
    """
    risk: RiskLevel = "medium"
    reason = getattr(decision, "decision_reason", None)
    if reason is not None:
        maybe_risk = getattr(reason, "risk", None)
        if maybe_risk in ("low", "medium", "high"):
            risk = maybe_risk
    return risk
