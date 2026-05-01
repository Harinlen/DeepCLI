"""Built-in tool that lets the LLM manage a session task list.

Pattern mirrors Claude Code's ``TodoWrite``: every invocation
receives the **complete** updated list and overwrites the stored
copy.  This trades a few extra tokens for a dead-simple storage
model — no patches, no sequencing, no conflict resolution.

The tool itself is **stateless** (safe to share across sessions in
the global tool registry).  It returns a
:class:`~daemon.side_effects.TasksUpdated` side-effect carrying the
full list; the orchestrator holds the per-session
:class:`TaskStore`, persists the list there, and emits a
:class:`~daemon.engine.stream.TaskUpdate` event to WS clients.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.side_effects import TasksUpdated
from daemon.tasks.store import TaskItem


class _TaskItemInput(BaseModel):
    """A single task, as supplied by the LLM."""

    content: str = Field(min_length=1)
    status: Literal["pending", "in_progress", "completed"]
    active_form: str = Field(min_length=1)


class TodoWriteTool(Tool):
    """Create and overwrite the session's task list.

    The LLM passes the **complete** list on every call; the stored
    list is replaced wholesale.  Keep only one task ``in_progress``
    at a time, and mark tasks ``completed`` as soon as they finish.
    """

    name = "todo_write"
    description = (
        "Create and update a task list to track work progress. Pass the "
        "COMPLETE task list each time — it overwrites the stored list. "
        "Each task needs: content (imperative), status "
        "(pending/in_progress/completed), and active_form (present "
        "continuous). Only one task should be in_progress at a time."
    )
    permission_level = PermissionLevel.NONE
    # Output is a short summary line.
    max_result_chars = 1_000

    class Input(BaseModel):
        """Wrapper around the list of task entries."""

        todos: list[_TaskItemInput] = Field(
            description="Complete task list — overwrites the stored list.",
        )

    async def execute(
        self,
        params: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        """Validate and hand the list off via the side-effect channel."""
        parsed = self.Input.model_validate(params)

        # The inner _TaskItemInput and TaskItem share identical fields;
        # convert via the Pydantic dump→validate path so the canonical
        # TaskItem model is what flows through the side-effect ADT.
        items = [
            TaskItem(
                content=t.content,
                status=t.status,
                active_form=t.active_form,
            )
            for t in parsed.todos
        ]

        summary = _summarize(parsed.todos)
        return ToolResult(
            output=summary,
            side_effect=TasksUpdated(tasks=items),
        )


def _summarize(tasks: list[_TaskItemInput]) -> str:
    """Produce a short human-readable summary for the LLM."""
    if not tasks:
        return "Task list cleared."

    pending = sum(1 for t in tasks if t.status == "pending")
    running = sum(1 for t in tasks if t.status == "in_progress")
    done = sum(1 for t in tasks if t.status == "completed")
    return (
        f"Tasks updated ({pending} pending, {running} in progress, "
        f"{done} completed, {len(tasks)} total)."
    )
