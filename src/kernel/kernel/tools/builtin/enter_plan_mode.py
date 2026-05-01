"""EnterPlanModeTool — lets the LLM switch into plan mode."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
)


class EnterPlanModeTool(Tool[dict[str, Any], None]):
    """Switch the session into plan mode (read-only exploration + planning)."""

    name = "EnterPlanMode"
    description_key = "tools/enter_plan_mode"
    description = "Enter plan mode for non-trivial implementation tasks."
    kind = ToolKind.other
    should_defer = True
    search_hint = "plan mode enter planning read-only explore design"

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="entering plan mode is a safe mode transition",
        )

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        # Gap 10: sub-agents cannot enter plan mode.
        if ctx.agent_depth > 0:
            msg = "EnterPlanMode cannot be used in sub-agent contexts"
            yield ToolCallResult(
                data={"error": msg},
                llm_content=[TextBlock(type="text", text=msg)],
                display=TextDisplay(text=msg),
            )
            return

        if ctx.set_mode is None:
            msg = "mode switching is not available in this session"
            yield ToolCallResult(
                data={"error": msg},
                llm_content=[TextBlock(type="text", text=msg)],
                display=TextDisplay(text=msg),
            )
            return

        # Gap 13: non-interactive sessions cannot enter plan mode
        # (ExitPlanMode requires user confirmation — no UI = trap).
        if ctx.interactive is False:
            msg = (
                "plan mode is not available in non-interactive sessions "
                "(ExitPlanMode requires user confirmation)"
            )
            yield ToolCallResult(
                data={"error": msg},
                llm_content=[TextBlock(type="text", text=msg)],
                display=TextDisplay(text=msg),
            )
            return

        ctx.set_mode("plan")

        msg = (
            "Entered plan mode. You should now focus on exploring the codebase "
            "and designing an implementation approach.\n\n"
            "In plan mode, you should:\n"
            "1. Thoroughly explore the codebase to understand existing patterns\n"
            "2. Design a concrete implementation strategy\n"
            "3. Use AskUserQuestion if you need to clarify the approach\n"
            "4. When ready, use ExitPlanMode to present your plan for approval\n\n"
            "Remember: DO NOT write or edit any files except the plan file. "
            "This is a read-only exploration and planning phase."
        )
        yield ToolCallResult(
            data={"status": "entered_plan_mode"},
            llm_content=[TextBlock(type="text", text=msg)],
            display=TextDisplay(text="Entered plan mode"),
        )


__all__ = ["EnterPlanModeTool"]
