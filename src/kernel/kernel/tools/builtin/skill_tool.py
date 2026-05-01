"""Skill — invoke a skill within the conversation.

The SkillTool is how the LLM activates skills.  When called, it:

1. Looks up the skill by name in SkillManager.
2. Checks setup requirements (Hermes env flow).
3. Activates the skill → returns rendered body.
4. The body is returned as the tool result, which the LLM treats as
   domain-specific instructions to follow.

Aligned with Claude Code's ``SkillTool.ts``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from kernel.orchestrator.types import ToolKind
from kernel.protocol.interfaces.contracts.text_block import TextBlock
from kernel.tools.context import ToolContext
from kernel.tools.tool import RiskContext, Tool
from kernel.tools.types import (
    PermissionSuggestion,
    TextDisplay,
    ToolCallProgress,
    ToolCallResult,
    ToolInputError,
)

logger = logging.getLogger(__name__)


class SkillTool(Tool[dict[str, Any], dict[str, Any]]):
    """Execute a skill within the main conversation.

    Skills provide specialized capabilities and domain knowledge.
    When users reference a slash command (``/skill-name``), they are
    referring to a skill that should be invoked via this tool.
    """

    name = "Skill"
    description_key = "tools/skill"
    description = "Execute a skill within the main conversation."
    kind = ToolKind.other
    is_concurrency_safe = False  # Only one skill at a time.

    input_schema = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": 'The skill name. E.g., "commit", "review-pr", or "pdf"',
            },
            "args": {
                "type": "string",
                "description": "Optional arguments for the skill",
            },
        },
        "required": ["skill"],
    }

    # ------------------------------------------------------------------
    # Tool ABC
    # ------------------------------------------------------------------

    def default_risk(self, input: dict[str, Any], ctx: RiskContext) -> PermissionSuggestion:
        """Skills without allowed-tools or hooks are safe by default.

        Skills that declare ``allowed-tools`` or ``hooks`` may alter
        permissions, so they require user confirmation.
        """
        skill_name = self._normalize_name(input)
        skills_mgr = self._get_skills_manager(ctx)
        if skills_mgr is None:
            return PermissionSuggestion(
                risk="low",
                default_decision="allow",
                reason="skill subsystem unavailable",
            )

        skill = skills_mgr.lookup(skill_name)
        if skill is None:
            return PermissionSuggestion(
                risk="low",
                default_decision="allow",
                reason="skill not found",
            )

        # Safe-properties check: no allowed_tools / hooks → auto-allow.
        has_dangerous = bool(skill.manifest.allowed_tools) or bool(skill.manifest.hooks)
        if has_dangerous:
            return PermissionSuggestion(
                risk="medium",
                default_decision="ask",
                reason=f"skill {skill_name!r} declares allowed-tools or hooks",
            )

        return PermissionSuggestion(
            risk="low",
            default_decision="allow",
            reason="skill has only safe properties",
        )

    def is_destructive(self, _input: dict[str, Any]) -> bool:
        return False

    async def validate_input(self, input: dict[str, Any], ctx: RiskContext) -> None:
        skill_name = self._normalize_name(input)
        if not skill_name:
            raise ToolInputError("skill must be a non-empty string")

        # We can't check the registry during validate_input because
        # RiskContext doesn't carry the skills manager.  Full validation
        # happens in call().

    async def call(
        self,
        input: dict[str, Any],
        ctx: ToolContext,
    ) -> AsyncGenerator[ToolCallProgress | ToolCallResult, None]:
        skill_name = self._normalize_name(input)
        args = str(input.get("args", "") or "")

        skills_mgr = self._get_skills_manager(ctx)
        if skills_mgr is None:
            yield self._error_result("Skill subsystem is not available")
            return

        # Lookup.
        skill = skills_mgr.lookup(skill_name)
        if skill is None:
            yield self._error_result(f"Unknown skill: {skill_name}")
            return

        # Model invocation check.
        if skill.manifest.disable_model_invocation:
            yield self._error_result(
                f"Skill {skill_name!r} cannot be invoked by the model "
                f"(disable-model-invocation is set)"
            )
            return

        # Activate.
        result = skills_mgr.activate(skill_name, args)
        if result is None:
            yield self._error_result(f"Failed to activate skill: {skill_name}")
            return

        # Setup needed (Hermes flow).
        if result.setup_needed:
            msg = result.setup_message or f"Skill {skill_name!r} requires setup."
            yield ToolCallResult(
                data={
                    "success": False,
                    "commandName": skill_name,
                    "setup_needed": True,
                },
                llm_content=[TextBlock(type="text", text=msg)],
                display=TextDisplay(text=msg),
            )
            return

        # Register skill-scoped hooks (HookManager integration).
        if result.hooks:
            self._register_skill_hooks(ctx, skill_name, result.hooks)

        # Success — return the skill body as tool result.
        # The LLM will read this as domain-specific instructions.
        yield ToolCallResult(
            data={
                "success": True,
                "commandName": skill_name,
                "allowedTools": list(result.allowed_tools) if result.allowed_tools else None,
                "model": result.model,
            },
            llm_content=[TextBlock(type="text", text=result.body)],
            display=TextDisplay(text=f"Skill {skill_name!r} activated"),
        )

    # ------------------------------------------------------------------
    # Prompt (tool description for skill listing)
    # ------------------------------------------------------------------

    def get_tool_prompt(self) -> str:
        """Extended prompt text appended to the tool description.

        Guides the LLM on how to use the Skill tool, aligned with
        Claude Code's ``prompt.ts``.
        """
        return (
            "Execute a skill within the main conversation\n\n"
            "When users ask you to perform tasks, check if any of the available "
            "skills match. Skills provide specialized capabilities and domain knowledge.\n\n"
            'When users reference a "slash command" or "/<something>" '
            '(e.g., "/commit", "/review-pr"), they are referring to a skill. '
            "Use this tool to invoke it.\n\n"
            "How to invoke:\n"
            "- Use this tool with the skill name and optional arguments\n"
            "- Examples:\n"
            '  - skill: "pdf" - invoke the pdf skill\n'
            '  - skill: "commit", args: "-m \'Fix bug\'" - invoke with arguments\n'
            '  - skill: "review-pr", args: "123" - invoke with arguments\n\n'
            "Important:\n"
            "- Available skills are listed in system-reminder messages in the conversation\n"
            "- When a skill matches the user's request, invoke it BEFORE generating "
            "any other response about the task\n"
            "- NEVER mention a skill without actually calling this tool\n"
            "- Do not invoke a skill that is already running\n"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_name(input: dict[str, Any]) -> str:
        """Extract and normalize skill name from input."""
        raw = str(input.get("skill", "")).strip()
        # Remove leading slash for compatibility.
        if raw.startswith("/"):
            raw = raw[1:]
        return raw

    @staticmethod
    def _get_skills_manager(ctx: Any) -> Any:
        """Get the SkillManager from the tool context.

        Returns None if skills subsystem is unavailable.
        ``ToolContext`` carries ``module_table`` which has the
        SkillManager when it's loaded.
        """
        try:
            module_table = ctx.module_table
            if module_table is None:
                return None
            from kernel.skills import SkillManager

            if module_table.has(SkillManager):
                return module_table.get(SkillManager)
        except (AttributeError, KeyError, ImportError):
            pass
        return None

    @staticmethod
    def _register_skill_hooks(ctx: Any, skill_name: str, hooks_config: dict[str, Any]) -> None:
        """Register skill-scoped hooks with HookManager.

        Skill hooks are defined in the ``hooks`` frontmatter field.
        They follow the same schema as global hooks but are scoped
        to the skill's activation lifetime.

        This is a best-effort operation — if HookManager is unavailable
        or the hooks config is invalid, we log and continue.
        """
        try:
            module_table = ctx.module_table
            if module_table is None:
                return
            from kernel.hooks import HookManager

            if not module_table.has(HookManager):
                return

            # TODO: HookManager needs a register_skill_hooks() method
            # to accept runtime hook definitions from skill frontmatter.
            # For now, log the intent and skip — the hook registration
            # mechanism requires further design (hook handlers from
            # frontmatter are shell commands, not Python handlers).
            logger.debug(
                "skills: skill %r declares hooks (not yet wired): %s",
                skill_name,
                list(hooks_config.keys()),
            )
        except (AttributeError, KeyError, ImportError):
            pass

    @staticmethod
    def _error_result(message: str) -> ToolCallResult:
        return ToolCallResult(
            data={"success": False, "error": message},
            llm_content=[TextBlock(type="text", text=message)],
            display=TextDisplay(text=message),
        )


__all__ = ["SkillTool"]
