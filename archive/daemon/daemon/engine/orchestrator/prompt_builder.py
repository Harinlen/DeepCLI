"""System prompt assembly for each LLM round.

Owns the per-session static context (cwd, git status, active skill)
and delegates memory index building to :class:`MemoryManager`.
The :class:`Orchestrator` calls :meth:`build_for_round` once per
tool-loop iteration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from daemon.engine.context import PromptSection, build_system_prompt

if TYPE_CHECKING:
    from daemon.engine.orchestrator.memory_manager import MemoryManager
    from daemon.engine.orchestrator.plan_mode import PlanModeController

logger = logging.getLogger(__name__)


class SystemPromptBuilder:
    """Assembles the per-round system prompt from static + dynamic context.

    Args:
        cwd: Working directory for environment info and AGENTS.md.
        git_status_sentinel: Sentinel object for "not yet fetched".
    """

    # Sentinel distinguishing "not yet fetched" from "fetched, was None".
    GIT_STATUS_UNSET: object = object()

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._git_status: object = self.GIT_STATUS_UNSET
        self._active_skill_prompt: str | None = None

    # -- Mutators ----------------------------------------------------------

    def set_git_status(self, status: str | None) -> None:
        """Set the git status snapshot (lazy-loaded on first query)."""
        self._git_status = status

    def invalidate_git_status(self) -> None:
        """Force re-fetch on next query (e.g. after session resume)."""
        self._git_status = self.GIT_STATUS_UNSET

    @property
    def git_status_needs_fetch(self) -> bool:
        """``True`` when git status hasn't been fetched yet."""
        return self._git_status is self.GIT_STATUS_UNSET

    @property
    def git_status(self) -> str | None:
        """Current git status, or ``None`` if not fetched / not a repo."""
        if self._git_status is self.GIT_STATUS_UNSET:
            return None
        return self._git_status  # type: ignore[return-value]

    def set_active_skill(self, prompt: str | None) -> None:
        """Set the active skill prompt (or clear it)."""
        self._active_skill_prompt = prompt

    # -- Build -------------------------------------------------------------

    async def build_for_round(
        self,
        *,
        model: str | None,
        model_id: str | None = None,
        knowledge_cutoff: str | None = None,
        identity_lines: list[str] | None = None,
        skill_info: list[tuple[str, str, str | None]] | None,
        memory_manager: MemoryManager | None,
        plan_mode: PlanModeController,
        user_message: str | None = None,
        lazy_tool_names: list[str] | None = None,
        mcp_server_names: list[str] | None = None,
        task_notifications: list[str] | None = None,
    ) -> list[PromptSection]:
        """Assemble the system prompt for one tool-loop round.

        Args:
            model: Active model display name.
            model_id: Exact provider model identifier.
            knowledge_cutoff: Training data cutoff string.
            identity_lines: Provider-specific identity lines for the
                environment section (model family info, etc.).
            skill_info: Available skills for system prompt.
            memory_manager: Memory manager for index building.
            plan_mode: Plan mode controller for instructions.
            user_message: Current user message for memory relevance
                ranking.  ``None`` on follow-up rounds.
        """
        git = None if self._git_status is self.GIT_STATUS_UNSET else self._git_status
        in_plan = plan_mode.in_plan_mode

        memory_index: str | None = None
        if memory_manager is not None:
            memory_index = await memory_manager.build_index(user_message)

        sections = build_system_prompt(
            cwd=self._cwd,
            model_name=model or "unknown",
            model_id=model_id,
            knowledge_cutoff=knowledge_cutoff,
            identity_lines=identity_lines,
            skill_info=skill_info,
            active_skill_prompt=self._active_skill_prompt,
            git_status=git,  # type: ignore[arg-type]
            memory_index=memory_index,
            plan_mode=in_plan,
            plan_mode_first_turn=plan_mode.first_turn,
            lazy_tool_names=lazy_tool_names,
            mcp_server_names=mcp_server_names,
        )

        if in_plan and plan_mode.first_turn:
            plan_mode.consume_first_turn()

        # Inject background task completion notifications.
        if task_notifications:
            for note in task_notifications:
                sections.append(
                    PromptSection(text=f"<system-reminder>\n{note}\n</system-reminder>")
                )

        return sections
