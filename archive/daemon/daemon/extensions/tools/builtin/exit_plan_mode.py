"""ExitPlanModeTool — finalize the plan and restore the prior mode.

Called by the LLM once its implementation plan is ready for user
review.  The tool hands the markdown plan off to the orchestrator
via an :class:`~daemon.side_effects.ExitPlanMode` side-effect,
which restores the pre-plan mode and persists the plan under the
session directory.

The tool's ``output`` is what the LLM sees next turn — framed as
confirmation of user approval so the LLM naturally transitions
into implementation.  The persisted file lives at
``{session_dir}/{session_id}.plan.md``.

Description text is ported verbatim from Claude Code's
``ExitPlanModeTool/prompt.ts``.  Unlike Claude Code's version —
which reads the plan from a file written during plan mode —
Mustang takes the ``plan`` markdown as a tool parameter.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.side_effects import ExitPlanMode


_DESCRIPTION = """Use this tool when you are in plan mode and have finished designing your implementation plan and are ready for user approval.

## How This Tool Works
- This tool takes your finalized plan as a `plan` parameter (markdown text)
- This tool simply signals that you're done planning and ready for the user to review and approve
- The user will see the contents of your plan when they review it

## When to Use This Tool
IMPORTANT: Only use this tool when the task requires planning the implementation steps of a task that requires writing code. For research tasks where you're gathering information, searching files, reading files or in general trying to understand the codebase - do NOT use this tool.

## Before Using This Tool
Ensure your plan is complete and unambiguous:
- If you have unresolved questions about requirements or approach, use ask_user_question first (in earlier phases)
- Once your plan is finalized, use THIS tool to request approval

**Important:** Do NOT use ask_user_question to ask "Is this plan okay?" or "Should I proceed?" - that's exactly what THIS tool does. ExitPlanMode inherently requests user approval of your plan.

## Examples

1. Initial task: "Search for and understand the implementation of vim mode in the codebase" - Do not use the exit plan mode tool because you are not planning the implementation steps of a task.
2. Initial task: "Help me implement yank mode for vim" - Use the exit plan mode tool after you have finished planning the implementation steps of the task.
3. Initial task: "Add a new feature to handle user authentication" - If unsure about auth method (OAuth, JWT, etc.), use ask_user_question first, then use exit plan mode tool after clarifying the approach."""


class ExitPlanModeTool(Tool):
    """Present the implementation plan and exit read-only mode."""

    name = "exit_plan_mode"
    description = _DESCRIPTION
    permission_level = PermissionLevel.NONE
    defer_execution = True
    max_result_chars = 20_000

    class Input(BaseModel):
        """Single-field input carrying the final plan."""

        plan: str = Field(
            min_length=1,
            description="Full implementation plan in markdown.",
        )

    async def execute(
        self,
        params: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        """Hand the plan off to the orchestrator via a side-effect."""
        parsed = self.Input.model_validate(params)
        plan = parsed.plan.strip()

        # Verbatim from Claude Code's ExitPlanModeV2Tool.ts (non-empty plan path).
        return ToolResult(
            output=(
                "User has approved your plan. You can now start coding. "
                "Start with updating your todo list if applicable\n\n"
                "## Approved Plan:\n\n"
                f"{plan}"
            ),
            side_effect=ExitPlanMode(plan=plan),
        )
