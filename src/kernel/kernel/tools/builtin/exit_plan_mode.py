"""ExitPlanModeTool — lets the LLM leave plan mode and resume execution."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from kernel.orchestrator.types import ToolKind
from kernel.plans import get_plan, get_plan_file_path
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
)


class ExitPlanModeTool(Tool[dict[str, Any], None]):
    """Leave plan mode and return to normal execution."""

    name = "ExitPlanMode"
    description_key = "tools/exit_plan_mode"
    description = "Exit plan mode and submit plan for user approval."
    kind = ToolKind.other
    should_defer = True
    search_hint = "plan mode exit leave resume execute implement"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    # Gap 11: ExitPlanMode requires user confirmation.
    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="ask",
            reason="exiting plan mode requires user approval of the plan",
        )

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        if ctx.set_mode is None:
            msg = "mode switching is not available in this session"
            yield ToolCallResult(
                data={"error": msg},
                llm_content=[TextBlock(type="text", text=msg)],
                display=TextDisplay(text=msg),
            )
            return

        # Read plan file before exiting (Gap 12).
        plan_content = get_plan(ctx.session_id, ctx.agent_id)
        plan_file = str(get_plan_file_path(ctx.session_id, ctx.agent_id))

        # Use "restore" sentinel to restore pre-plan mode (Gap 2).
        ctx.set_mode("restore")

        # Build response with plan content (Gap 12).
        if plan_content and plan_content.strip():
            msg = (
                "User has approved your plan. You can now start coding. "
                "Start with updating your todo list if applicable\n\n"
                f"Your plan has been saved to: {plan_file}\n"
                "You can refer back to it if needed during implementation.\n\n"
                f"## Approved Plan:\n{plan_content}"
            )
        else:
            msg = (
                "Exited plan mode. You can now make edits, run tools, "
                "and take actions. Proceed with implementing the approved plan."
            )

        yield ToolCallResult(
            data={
                "status": "exited_plan_mode",
                "plan_file": plan_file,
                "has_plan": plan_content is not None and bool(plan_content.strip()),
                # Gap 14: reserved for future team approval workflow.
                "awaiting_approval": False,
                "request_id": None,
            },
            llm_content=[TextBlock(type="text", text=msg)],
            display=TextDisplay(text="Exited plan mode"),
        )


__all__ = ["ExitPlanModeTool"]
