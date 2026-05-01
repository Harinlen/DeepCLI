"""SkillTool — lets the LLM activate a skill by name.

When invoked, loads the skill body (lazy), substitutes ``$ARGUMENTS``,
and returns the rendered prompt as the tool result.  The orchestrator
then injects this prompt into the system prompt for subsequent LLM
turns.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from daemon.extensions.skills.base import render_skill_body
from daemon.extensions.skills.loader import load_skill_body
from daemon.extensions.skills.registry import SkillRegistry
from daemon.extensions.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
)
from daemon.side_effects import SkillActivated

logger = logging.getLogger(__name__)


class SkillTool(Tool):
    """Activate a skill by name, injecting its prompt into the conversation.

    The LLM calls this tool to load a skill's prompt template.  The
    rendered prompt is returned as the tool result and the orchestrator
    uses it to augment the system prompt for subsequent turns.
    """

    name = "skill"
    description = (
        "Activate a skill by name. Returns the skill's prompt which will "
        "be injected into your system context for subsequent turns. "
        "Use this when a task matches an available skill's purpose."
    )
    permission_level = PermissionLevel.NONE

    class Input(BaseModel):
        """Parameters for the skill tool."""

        name: str = Field(min_length=1, description="Name of the skill to activate.")
        arguments: str = Field(
            default="",
            description="Arguments to pass to the skill (substituted for $ARGUMENTS).",
        )

    def __init__(self, skill_registry: SkillRegistry) -> None:
        self._skill_registry = skill_registry

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Activate a skill and return its rendered prompt.

        Args:
            params: Must contain ``name``; optionally ``arguments``.
            ctx: Execution context (unused).

        Returns:
            ToolResult with the rendered skill prompt, or an error if
            the skill is not found or cannot be loaded.
        """
        validated = self.Input.model_validate(params)

        skill = self._skill_registry.get(validated.name)
        if skill is None:
            available = ", ".join(self._skill_registry.skill_names) or "(none)"
            return ToolResult(
                output=f"Skill '{validated.name}' not found. Available: {available}",
                is_error=True,
            )

        # Lazy-load the body from disk
        try:
            load_skill_body(skill)
        except OSError as exc:
            return ToolResult(
                output=f"Cannot load skill '{validated.name}': {exc}",
                is_error=True,
            )

        rendered = render_skill_body(skill, validated.arguments)

        logger.info("Activated skill '%s'", validated.name)
        # Emit a typed side-effect so the orchestrator can stash the
        # rendered body for injection next turn without name-matching
        # on the tool.  See docs/lessons-learned.md §Phase 4.X Audit.
        return ToolResult(
            output=rendered,
            side_effect=SkillActivated(prompt=rendered),
        )
