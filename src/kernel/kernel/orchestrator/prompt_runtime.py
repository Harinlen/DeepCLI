"""Dynamic system-prompt augmentation for StandardOrchestrator."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from kernel.llm.types import PromptSection
from kernel.orchestrator.runtime import PlanPromptRuntime, QueryRuntime, system_reminder_section

logger = logging.getLogger(__name__)

TURNS_BETWEEN_ATTACHMENTS = 5
FULL_REMINDER_EVERY_N = 5


def dump_system_prompt(sections: list[PromptSection], session_id: str, model: object) -> None:
    """Write the exact system prompt to MUSTANG_DUMP_SYSTEM_PROMPT.

    Args:
        sections: Prompt sections built for the first turn.
        session_id: Session id included in debug logs.
        model: Active model reference included in the dump header.

    Returns:
        ``None``.
    """
    dest = os.environ.get("MUSTANG_DUMP_SYSTEM_PROMPT", "")
    if not dest:
        return
    header = (
        f"Turn 1 | {time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())} | model={model}\n\n---\n\n"
    )
    try:
        Path(dest).write_text(header + "\n\n".join(sec.text for sec in sections), encoding="utf-8")
        logger.info("System prompt dumped to %s (%d sections)", dest, len(sections))
    except OSError:
        logger.warning("MUSTANG_DUMP_SYSTEM_PROMPT: could not write to %s", dest)


def build_session_guidance(
    orchestrator: QueryRuntime,
    enabled_tools: set[str],
    has_skills: bool,
) -> str | None:
    """Build CC-style session-specific guidance bullets.

    Args:
        orchestrator: Runtime providing PromptManager and subsystem access.
        enabled_tools: Tool names visible in the current tool snapshot.
        has_skills: Whether skill guidance should be considered.

    Returns:
        Rendered guidance text, or ``None`` when no bullets apply.
    """
    prompts = orchestrator._deps.prompts
    if prompts is None:
        return None

    def get(key: str) -> str | None:
        """Load one optional session-guidance prompt fragment.

        Args:
            key: Leaf key under ``orchestrator/session_guidance``.

        Returns:
            Prompt fragment text, or ``None`` when absent.
        """
        full = f"orchestrator/session_guidance/{key}"
        return prompts.get(full) if prompts.has(full) else None

    items: list[str] = []
    if "AskUserQuestion" in enabled_tools and (bullet := get("deny_ask")):
        items.append(bullet)
    if bullet := get("interactive_shell"):
        items.append(bullet)
    if "Agent" in enabled_tools:
        for key in ("agent_tool", "search_direct", "search_explore_agent"):
            if bullet := get(key):
                items.append(bullet)
    if has_skills and "Skill" in enabled_tools and (bullet := get("skill_invoke")):
        items.append(bullet)
    if not items:
        return None
    return "# Session-specific guidance\n" + "\n".join(f" - {item}" for item in items)


def inject_session_guidance(
    orchestrator: QueryRuntime,
    system_prompt: list[PromptSection],
    snapshot_tool_names: set[str],
) -> None:
    """Append session-specific guidance based on available tools.

    Args:
        orchestrator: Runtime providing prompts, skills, and deps.
        system_prompt: Mutable prompt section list for the current turn.
        snapshot_tool_names: Tool names visible to the model this turn.

    Returns:
        ``None``.
    """
    has_skills = orchestrator._deps.skills is not None and bool(
        orchestrator._deps.skills.get_skill_listing()
    )
    text = build_session_guidance(orchestrator, snapshot_tool_names, has_skills)
    if text is not None:
        system_prompt.append(PromptSection(text=text, cache=False))


def inject_plan_mode_prompts(
    orchestrator: PlanPromptRuntime,
    system_prompt: list[PromptSection],
) -> None:
    """Append plan-mode / exit / reentry reminders to ``system_prompt``.

    Args:
        orchestrator: Runtime carrying plan-mode counters and flags.
        system_prompt: Mutable prompt section list for the current turn.

    Returns:
        ``None``.
    """
    prompts = orchestrator._deps.prompts
    if prompts is None:
        return
    if not orchestrator.plan_mode:
        _inject_exit_notification(orchestrator, system_prompt)
        return

    orchestrator._plan_mode_turn_count += 1
    if orchestrator._plan_mode_attachment_count > 0:
        if orchestrator._plan_mode_turn_count < TURNS_BETWEEN_ATTACHMENTS:
            return
    orchestrator._plan_mode_turn_count = 0
    orchestrator._plan_mode_attachment_count += 1

    _inject_reentry_notification(orchestrator, system_prompt)
    plan_file_path = _plan_file_path(orchestrator)
    if (orchestrator._plan_mode_attachment_count % FULL_REMINDER_EVERY_N) == 1:
        from kernel.plans import get_plan

        if get_plan(orchestrator._session_id) is not None:
            info = (
                f"A plan file already exists at {plan_file_path}. "
                "You can read it and make incremental edits using the FileEdit tool."
            )
        else:
            info = (
                "No plan file exists yet. You should create your plan at "
                f"{plan_file_path} using the FileWrite tool."
            )
        text = prompts.render("orchestrator/plan_mode", plan_file_info=info)
    else:
        text = prompts.render("orchestrator/plan_mode_sparse", plan_file_path=str(plan_file_path))
    if text:
        system_prompt.append(system_reminder_section(text))


def _inject_exit_notification(
    orchestrator: PlanPromptRuntime,
    system_prompt: list[PromptSection],
) -> None:
    """Append the one-shot exit-plan-mode reminder.

    Args:
        orchestrator: Runtime carrying exit notification state.
        system_prompt: Mutable prompt section list for the current turn.

    Returns:
        ``None``.
    """
    if not orchestrator._needs_plan_mode_exit_attachment:
        return
    prompts = orchestrator._deps.prompts
    if prompts is None:
        return
    orchestrator._needs_plan_mode_exit_attachment = False
    text = prompts.render(
        "orchestrator/plan_mode_exit",
        plan_file_path=str(_plan_file_path(orchestrator)),
    )
    if text:
        system_prompt.append(system_reminder_section(text))


def _inject_reentry_notification(
    orchestrator: PlanPromptRuntime,
    system_prompt: list[PromptSection],
) -> None:
    """Append the one-shot plan reentry reminder when a plan exists.

    Args:
        orchestrator: Runtime carrying reentry notification state.
        system_prompt: Mutable prompt section list for the current turn.

    Returns:
        ``None``.
    """
    if not orchestrator._has_exited_plan_mode:
        return
    from kernel.plans import get_plan

    if get_plan(orchestrator._session_id) is None:
        return
    prompts = orchestrator._deps.prompts
    if prompts is None:
        return
    orchestrator._has_exited_plan_mode = False
    text = prompts.render(
        "orchestrator/plan_mode_reentry",
        plan_file_path=str(_plan_file_path(orchestrator)),
    )
    if text:
        system_prompt.append(system_reminder_section(text))


def _plan_file_path(orchestrator: QueryRuntime) -> str:
    """Return the session's durable plan file path.

    Args:
        orchestrator: Runtime whose session id selects the plan file.

    Returns:
        Path string returned by the plan subsystem.
    """
    from kernel.plans import get_plan_file_path

    return str(get_plan_file_path(orchestrator._session_id))
