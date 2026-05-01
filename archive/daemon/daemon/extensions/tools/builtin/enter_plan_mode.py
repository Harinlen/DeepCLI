"""EnterPlanModeTool — switch the session into read-only planning.

The LLM calls this tool when the user's request needs upfront
exploration and design before any code changes.  Invocation is
essentially free (no params, no output text of substance) — the
real work happens in the orchestrator, driven by the
:class:`~daemon.side_effects.EnterPlanMode` side-effect we return.

Plan mode restricts the tool set to ``file_read``, ``glob``,
``grep`` plus the plan-file for incremental edits
(see :class:`~daemon.permissions.engine.PermissionEngine._check_plan_mode`).
Exit via :class:`ExitPlanModeTool`.

Description text is ported verbatim from Claude Code's
``EnterPlanModeTool/prompt.ts`` to match the same triggering
behavior.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.side_effects import EnterPlanMode


_DESCRIPTION = """Use this tool proactively when you're about to start a non-trivial implementation task. Getting user sign-off on your approach before writing code prevents wasted effort and ensures alignment. This tool transitions you into plan mode where you can explore the codebase and design an implementation approach for user approval.

## When to Use This Tool

**Prefer using EnterPlanMode** for implementation tasks unless they're simple. Use it when ANY of these conditions apply:

1. **New Feature Implementation**: Adding meaningful new functionality
   - Example: "Add a logout button" - where should it go? What should happen on click?
   - Example: "Add form validation" - what rules? What error messages?

2. **Multiple Valid Approaches**: The task can be solved in several different ways
   - Example: "Add caching to the API" - could use Redis, in-memory, file-based, etc.
   - Example: "Improve performance" - many optimization strategies possible

3. **Code Modifications**: Changes that affect existing behavior or structure
   - Example: "Update the login flow" - what exactly should change?
   - Example: "Refactor this component" - what's the target architecture?

4. **Architectural Decisions**: The task requires choosing between patterns or technologies
   - Example: "Add real-time updates" - WebSockets vs SSE vs polling
   - Example: "Implement state management" - Redux vs Context vs custom solution

5. **Multi-File Changes**: The task will likely touch more than 2-3 files
   - Example: "Refactor the authentication system"
   - Example: "Add a new API endpoint with tests"

6. **Unclear Requirements**: You need to explore before understanding the full scope
   - Example: "Make the app faster" - need to profile and identify bottlenecks
   - Example: "Fix the bug in checkout" - need to investigate root cause

7. **User Preferences Matter**: The implementation could reasonably go multiple ways
   - If you would use AskUserQuestion to clarify the approach, use EnterPlanMode instead
   - Plan mode lets you explore first, then present options with context

## When NOT to Use This Tool

Only skip EnterPlanMode for simple tasks:
- Single-line or few-line fixes (typos, obvious bugs, small tweaks)
- Adding a single function with clear requirements
- Tasks where the user has given very specific, detailed instructions
- Pure research/exploration tasks

## Examples

### GOOD - Use EnterPlanMode:
User: "Add user authentication to the app"
- Requires architectural decisions (session vs JWT, where to store tokens, middleware structure)

User: "Optimize the database queries"
- Multiple approaches possible, need to profile first, significant impact

User: "Implement dark mode"
- Architectural decision on theme system, affects many components

User: "Add a delete button to the user profile"
- Seems simple but involves: where to place it, confirmation dialog, API call, error handling, state updates

User: "Update the error handling in the API"
- Affects multiple files, user should approve the approach

### BAD - Don't use EnterPlanMode:
User: "Fix the typo in the README"
- Straightforward, no planning needed

User: "Add a console.log to debug this function"
- Simple, obvious implementation

User: "What files handle routing?"
- Research task, not implementation planning

## Important Notes

- This tool REQUIRES user approval - they must consent to entering plan mode
- If unsure whether to use it, err on the side of planning - it's better to get alignment upfront than to redo work
- Users appreciate being consulted before significant changes are made to their codebase"""


# Confirmation message shown to the LLM after the user approves entry.
# Ported verbatim from Claude Code's EnterPlanModeTool.ts.
_ENTERED_MESSAGE = """Entered plan mode. You should now focus on exploring the codebase and designing an implementation approach.

In plan mode, you should:
1. Thoroughly explore the codebase to understand existing patterns
2. Identify similar features and architectural approaches
3. Consider multiple approaches and their trade-offs
4. Use AskUserQuestion if you need to clarify the approach
5. Design a concrete implementation strategy
6. When ready, use ExitPlanMode to present your plan for approval

Remember: DO NOT write or edit any files yet. This is a read-only exploration and planning phase."""


class EnterPlanModeTool(Tool):
    """Switch to read-only planning mode for codebase exploration."""

    name = "enter_plan_mode"
    description = _DESCRIPTION
    permission_level = PermissionLevel.NONE
    defer_execution = True
    max_result_chars = 5_000

    class Input(BaseModel):
        """No arguments — plan mode is a single toggle."""

        pass

    async def execute(
        self,
        params: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        """Request the orchestrator enter plan mode."""
        return ToolResult(
            output=_ENTERED_MESSAGE,
            side_effect=EnterPlanMode(),
        )
