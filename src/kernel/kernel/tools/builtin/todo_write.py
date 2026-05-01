"""TodoWriteTool — LLM-managed session task checklist.

Design reference: ``docs/plans/task-manager.md`` § 5.
Claude Code equivalent: ``src/tools/TodoWriteTool/TodoWriteTool.ts``.

Independent from the background task framework — TodoWrite is a pure
data checklist that the LLM uses to track its own progress.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import Tool
from kernel.tools.types import (
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)


class TodoWriteTool(Tool[dict[str, Any], str]):
    """Manage the session task checklist."""

    name = "TodoWrite"
    description_key = "tools/todo_write"
    description = (
        "Update the todo list for the current session. To be used proactively "
        "and often to track progress and pending tasks. Make sure that at "
        "least one task is in_progress at all times. Always provide both "
        "content (imperative) and activeForm (present continuous) for each "
        "task."
    )
    kind = ToolKind.other

    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Imperative form describing what needs to "
                                "be done, e.g. 'Run tests' or 'Build the "
                                "project'."
                            ),
                        },
                        "activeForm": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Present-continuous form shown while the "
                                "task is in progress, e.g. 'Running tests' "
                                "or 'Building the project'."
                            ),
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                    },
                    "required": ["content", "activeForm", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    }

    async def validate_input(self, input: dict[str, Any], ctx: Any) -> None:
        """Reject todo items missing ``content`` / ``activeForm`` / ``status``.

        The JSON schema already marks the three fields required, but some
        providers strip ``additionalProperties`` / ``required`` before
        calling.  Defensive validation keeps the LLM honest and the
        permission round-trip clean.
        """
        todos = input.get("todos")
        if not isinstance(todos, list):
            raise ToolInputError("'todos' must be a list")
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                raise ToolInputError(f"todos[{i}] must be an object")
            for key in ("content", "activeForm", "status"):
                val = item.get(key)
                if not isinstance(val, str) or not val.strip():
                    raise ToolInputError(
                        f"todos[{i}].{key} is required (non-empty string)"
                    )
            if item["status"] not in {"pending", "in_progress", "completed"}:
                raise ToolInputError(
                    f"todos[{i}].status must be one of "
                    "pending / in_progress / completed"
                )

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        todos: list[dict[str, str]] = input["todos"]
        all_done = all(t.get("status") == "completed" for t in todos)
        new_todos = [] if all_done else todos

        old_todos: list[dict[str, str]] = []
        if ctx.tasks is not None:
            old_todos = ctx.tasks.get_todos(ctx.agent_id)
            ctx.tasks.set_todos(ctx.agent_id, new_todos)

        body = "Todos have been modified successfully."
        yield ToolCallResult(
            data={"old_todos": old_todos, "new_todos": new_todos},
            llm_content=[TextBlock(type="text", text=body)],
            display=TextDisplay(text=body),
        )


__all__ = ["TodoWriteTool"]
