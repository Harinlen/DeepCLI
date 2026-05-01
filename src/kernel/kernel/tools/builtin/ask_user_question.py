"""AskUserQuestionTool — structured multi-choice questions via the permission channel.

Design (aligned with Claude Code ``AskUserQuestionTool.tsx``):

The tool **itself** performs no work.  Its ``default_risk`` returns
``ask``, which forces the ToolExecutor through the permission
round-trip.  The client renders a question UI instead of a normal
permission dialog (it recognises the tool name) and returns the
user's answers inside ``PermissionResponse.updated_input``.

The ToolExecutor forwards ``updated_input`` into
``PermissionAllow.updated_input`` → ``effective_input``, so by the
time ``call()`` runs the input dict already contains ``answers``.

``call()`` simply formats those answers into a human-readable
``ToolCallResult`` that the LLM can act on.
"""

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

# ---------------------------------------------------------------------------
# JSON Schema — mirrors Claude Code's Zod schema
# ---------------------------------------------------------------------------

_OPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "description": (
                "Display text for this option (1-5 words). Should clearly describe the choice."
            ),
        },
        "description": {
            "type": "string",
            "description": ("Explanation of what this option means or what will happen if chosen."),
        },
        "preview": {
            "type": "string",
            "description": (
                "Optional markdown preview content rendered when this "
                "option is focused. Use for mockups, code snippets, or "
                "visual comparisons."
            ),
        },
    },
    "required": ["label", "description"],
    "additionalProperties": False,
}

_CHOICE_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["choice"],
            "description": "Question type. Omit or use 'choice' for selectable choices.",
        },
        "question": {
            "type": "string",
            "description": (
                "The complete question to ask. Should be clear and end with a question mark."
            ),
        },
        "header": {
            "type": "string",
            "description": "Short label displayed as a chip/tag (max 12 chars).",
            "maxLength": 12,
        },
        "options": {
            "type": "array",
            "items": _OPTION_SCHEMA,
            "minItems": 2,
            "maxItems": 4,
            "description": (
                "Available choices (2-4 options). An 'Other' free-text "
                "option is always appended automatically by the client."
            ),
        },
        "multiSelect": {
            "type": "boolean",
            "description": ("When true the user may select multiple options. Default false."),
            "default": False,
        },
    },
    "required": ["question", "header", "options"],
    "additionalProperties": False,
}

_TEXT_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["text"],
            "description": "Use for open-ended questions that need free-form user text.",
        },
        "question": {
            "type": "string",
            "description": (
                "The complete question to ask. Should be clear and end with a question mark."
            ),
        },
        "header": {
            "type": "string",
            "description": "Short label displayed as a chip/tag (max 12 chars).",
            "maxLength": 12,
        },
        "placeholder": {
            "type": "string",
            "description": "Optional hint shown in the text input.",
        },
        "multiline": {
            "type": "boolean",
            "description": "When true the client may allow a multi-line answer. Default false.",
            "default": False,
        },
        "maxLength": {
            "type": "integer",
            "minimum": 1,
            "maximum": 4000,
            "description": "Optional maximum answer length in characters.",
        },
    },
    "required": ["type", "question", "header"],
    "additionalProperties": False,
}

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {"anyOf": [_CHOICE_QUESTION_SCHEMA, _TEXT_QUESTION_SCHEMA]},
            "minItems": 1,
            "maxItems": 4,
            "description": "Questions to ask the user (1-4 questions).",
        },
        "answers": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": (
                "User answers, keyed by question text. "
                "Populated by the client via the permission round-trip."
            ),
        },
        "annotations": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "preview": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "description": (
                "Optional per-question annotations (preview selections, "
                "user notes). Keyed by question text."
            ),
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


class AskUserQuestionTool(Tool[dict[str, Any], dict[str, Any]]):
    """Ask the user structured multiple-choice questions.

    The real user interaction happens in the permission round-trip,
    not in ``call()``.  See module docstring for the full flow.
    """

    name = "AskUserQuestion"
    description_key = "tools/ask_user_question"
    description = "Ask the user structured multiple-choice questions."
    kind = ToolKind.other
    should_defer = True
    search_hint = "ask user question choice preference clarify decision"

    input_schema: ClassVar[dict[str, Any]] = _INPUT_SCHEMA

    # Read-only, concurrency-safe, not destructive.
    is_concurrency_safe = True  # type: ignore[assignment]

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        """Force the permission channel — this is how the question reaches the user."""
        return PermissionSuggestion(
            risk="low",
            default_decision="ask",
            reason="Asking user a question",
        )

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    def user_facing_name(self, _input: dict[str, Any]) -> str:
        return "Ask user"

    def activity_description(self, _input: dict[str, Any]) -> str | None:
        return "Asking the user a question"

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        """Validate question structure before the permission round-trip."""
        from kernel.tools.types import ToolInputError

        questions = input.get("questions")
        if not questions or not isinstance(questions, list):
            raise ToolInputError("'questions' must be a non-empty list")
        if len(questions) > 4:
            raise ToolInputError("at most 4 questions allowed")

        seen_texts: set[str] = set()
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                raise ToolInputError(f"questions[{i}] must be an object")
            text = q.get("question")
            if not text or not isinstance(text, str):
                raise ToolInputError(f"questions[{i}].question is required")
            if text in seen_texts:
                raise ToolInputError(f"duplicate question text: {text!r}")
            seen_texts.add(text)

            q_type = q.get("type", "choice")
            if q_type not in ("choice", "text"):
                raise ToolInputError(f"questions[{i}].type must be 'choice' or 'text'")

            if q_type == "text":
                self._validate_text_question(q, i)
                continue

            options = q.get("options")
            if not options or not isinstance(options, list) or len(options) < 2:
                raise ToolInputError(f"questions[{i}].options must have at least 2 items")
            if len(options) > 4:
                raise ToolInputError(f"questions[{i}].options must have at most 4 items")

            seen_labels: set[str] = set()
            for j, opt in enumerate(options):
                if not isinstance(opt, dict):
                    raise ToolInputError(f"questions[{i}].options[{j}] must be an object")
                label = opt.get("label")
                if not label or not isinstance(label, str):
                    raise ToolInputError(f"questions[{i}].options[{j}].label is required")
                if label in seen_labels:
                    raise ToolInputError(f"duplicate option label in questions[{i}]: {label!r}")
                seen_labels.add(label)

    def _validate_text_question(self, question: dict[str, Any], index: int) -> None:
        """Validate optional fields for a free-form text question."""
        from kernel.tools.types import ToolInputError

        placeholder = question.get("placeholder")
        if placeholder is not None and not isinstance(placeholder, str):
            raise ToolInputError(f"questions[{index}].placeholder must be a string")

        multiline = question.get("multiline")
        if multiline is not None and not isinstance(multiline, bool):
            raise ToolInputError(f"questions[{index}].multiline must be a boolean")

        max_length = question.get("maxLength")
        if max_length is None:
            return
        if not isinstance(max_length, int) or isinstance(max_length, bool):
            raise ToolInputError(f"questions[{index}].maxLength must be an integer")
        if max_length < 1 or max_length > 4000:
            raise ToolInputError(f"questions[{index}].maxLength must be between 1 and 4000")

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        """Format user answers into a tool result for the LLM.

        By this point ``input`` already contains ``answers`` (injected by
        the permission round-trip via ``PermissionResponse.updated_input``).
        """
        questions = input.get("questions", [])
        answers: dict[str, str] = input.get("answers", {})
        annotations: dict[str, Any] = input.get("annotations", {})

        # Build human-readable answer text for the LLM.
        parts: list[str] = []
        for q in questions:
            q_text = q.get("question", "")
            answer = answers.get(q_text, "(no answer)")
            parts.append(f"Q: {q_text}\nA: {answer}")

            # Include annotation notes if present.
            ann = annotations.get(q_text, {})
            if isinstance(ann, dict) and ann.get("notes"):
                parts.append(f"   Note: {ann['notes']}")

        answers_text = "\n\n".join(parts)
        llm_text = (
            f"User has answered your questions:\n\n{answers_text}\n\n"
            "You can now continue with the user's answers in mind."
        )

        yield ToolCallResult(
            data={"questions": questions, "answers": answers},
            llm_content=[TextBlock(type="text", text=llm_text)],
            display=TextDisplay(text=answers_text),
        )


__all__ = ["AskUserQuestionTool"]
