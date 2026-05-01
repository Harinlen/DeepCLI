"""AskUserQuestion tool — structured questions with options.

Sends questions to the user via the WebSocket protocol and returns
their selections.  Supports single-select, multi-select, and free
text ("Other") responses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from daemon.extensions.tools.base import (
    ConcurrencyHint,
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)

logger = logging.getLogger(__name__)


class AskUserQuestionTool(Tool):
    """Ask the user structured questions with options."""

    name = "ask_user_question"
    description = (
        "Ask the user a question with structured options. "
        "Use when you need the user to choose between alternatives "
        "or clarify their intent."
    )
    permission_level = PermissionLevel.NONE
    concurrency = ConcurrencyHint.SERIAL

    class QuestionOption(BaseModel):
        label: str = Field(..., max_length=80)
        description: str = Field(default="", max_length=200)

    class Question(BaseModel):
        question: str
        options: list[AskUserQuestionTool.QuestionOption] = Field(  # type: ignore[name-defined]
            ..., min_length=2, max_length=6
        )
        multi_select: bool = False

    class Input(BaseModel):
        questions: list[dict[str, Any]] = Field(
            ...,
            min_length=1,
            max_length=4,
            description="List of questions with options.",
        )

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        validated = self.Input.model_validate(params)

        if ctx.ask_user is None:
            return ToolResult(
                output="ask_user_question is not available in this context.",
                is_error=True,
            )

        try:
            response = await ctx.ask_user(validated.questions)
        except Exception as exc:
            logger.exception("ask_user callback failed")
            return ToolResult(
                output=f"Failed to get user response: {exc}",
                is_error=True,
            )

        return ToolResult(output=json.dumps(response))
