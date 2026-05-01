"""Agent tool — launch a sub-agent for autonomous task execution.

The sub-agent gets its own conversation and (optionally filtered)
tool set but shares the parent's provider registry and config.  The
orchestrator intercepts this tool's execution (via ``isinstance``
check) and runs the full child lifecycle — the ``execute()`` method
here is never called directly.

See ``docs/plans/pending/phase5-batch2.md`` §5.2 for the design.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)


class AgentTool(Tool):
    """Launch a sub-agent to handle a complex, multi-step task.

    The sub-agent runs in its own conversation context, executes
    the task, and returns its final response as the tool result.
    Useful for parallelizable research, independent code changes,
    or tasks that benefit from context isolation.

    The orchestrator intercepts this tool and manages the child
    lifecycle directly — ``execute()`` is a placeholder.
    """

    name = "agent"
    description = (
        "Launch a sub-agent to handle a complex, multi-step task autonomously. "
        "The sub-agent gets its own conversation and tool set, runs the task, "
        "and returns its final response. Use for parallelizable research, "
        "independent code changes, or tasks that benefit from isolation."
    )
    permission_level = PermissionLevel.PROMPT
    concurrency = ConcurrencyHint.PARALLEL
    max_result_chars: int | None = None  # Full sub-agent output

    class Input(BaseModel):
        """Parameters for the agent tool."""

        prompt: str = Field(
            min_length=1,
            description="Task description for the sub-agent.",
        )
        description: str = Field(
            default="",
            max_length=80,
            description="Short (3-5 word) summary shown in the UI.",
        )
        tools: list[str] | None = Field(
            default=None,
            description=(
                "Tool subset for the sub-agent. None inherits all "
                "tools minus 'agent' (at max depth)."
            ),
        )

        @field_validator("tools", mode="before")
        @classmethod
        def _coerce_tools(cls, v: Any) -> Any:
            """Tolerate stringified JSON arrays from LLMs that
            over-serialize list parameters (seen with Bedrock Claude).
            """
            if isinstance(v, str):
                stripped = v.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    try:
                        parsed = json.loads(stripped)
                        if isinstance(parsed, list):
                            return parsed
                    except json.JSONDecodeError:
                        pass
            return v
        permission_mode: str | None = Field(
            default=None,
            description=(
                "Permission mode for the sub-agent. Defaults to "
                "parent's mode. Use to narrow (e.g. 'plan')."
            ),
        )
        # P1/P2 reserved fields — not implemented in this batch.
        mode: Literal["sync"] = Field(
            default="sync",
            description="Execution mode. Only 'sync' is supported.",
        )
        isolation: Literal["none"] | None = Field(
            default=None,
            description="Isolation mode. Reserved for future worktree support.",
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Placeholder — the orchestrator intercepts agent calls.

        This method should never be called.  If it is, something
        bypassed the orchestrator's ``isinstance(tool, AgentTool)``
        check.
        """
        return ToolResult(
            output="Agent tool must be executed via the orchestrator, not directly.",
            is_error=True,
        )
