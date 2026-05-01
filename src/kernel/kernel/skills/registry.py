"""In-memory skill index.

Three pools:

- ``_skills`` — unconditional skills loaded at startup (static).
- ``_conditional`` — skills with ``paths`` frontmatter, waiting for
  file-operation activation.
- ``_dynamic`` — skills discovered at runtime via ``on_file_touched``
  or activated from the conditional pool.

Lookup order: dynamic → static.  Conditional skills are invisible
until activated (they move into ``_dynamic``).

Thread safety: the registry is only mutated during SkillManager
``startup()`` (single-threaded) and ``on_file_touched()`` (called
serially from ToolExecutor).  No locks needed.
"""

from __future__ import annotations

import logging

from kernel.skills.types import LoadedSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Name → LoadedSkill index with three pools."""

    def __init__(self) -> None:
        self._skills: dict[str, LoadedSkill] = {}
        self._conditional: dict[str, LoadedSkill] = {}
        self._dynamic: dict[str, LoadedSkill] = {}

    # ── Registration ─────────────────────────────────────────────

    def register(self, skill: LoadedSkill) -> None:
        """Add a skill to the static pool.

        Lower ``layer_priority`` wins — a project-layer skill with the
        same name as a user-layer skill is kept, the user-layer one is
        dropped (first-registered-wins since the loader scans in
        priority order).
        """
        name = skill.manifest.name
        existing = self._skills.get(name)
        if existing is not None:
            if skill.layer_priority >= existing.layer_priority:
                logger.debug(
                    "skills: ignoring %s from %s (already loaded from %s)",
                    name,
                    skill.source.value,
                    existing.source.value,
                )
                return
        self._skills[name] = skill

    def register_conditional(self, skill: LoadedSkill) -> None:
        """Add a skill to the conditional pool (has ``paths`` frontmatter)."""
        name = skill.manifest.name
        if name not in self._conditional:
            self._conditional[name] = skill

    def register_dynamic(self, skill: LoadedSkill) -> None:
        """Add a runtime-discovered skill to the dynamic pool."""
        self._dynamic[skill.manifest.name] = skill

    # ── Activation ───────────────────────────────────────────────

    def activate_conditional(self, name: str) -> LoadedSkill | None:
        """Move a conditional skill into the dynamic pool.

        Returns the activated skill, or None if it wasn't in the
        conditional pool.
        """
        skill = self._conditional.pop(name, None)
        if skill is not None:
            self._dynamic[name] = skill
            logger.info("skills: activated conditional skill %r", name)
        return skill

    # ── Lookup ───────────────────────────────────────────────────

    def lookup(self, name: str) -> LoadedSkill | None:
        """Look up a skill by name.  Dynamic > static."""
        return self._dynamic.get(name) or self._skills.get(name)

    def all_skills(self) -> list[LoadedSkill]:
        """All loaded skills (static + dynamic, excluding conditional).

        When the same name exists in both dynamic and static, the
        dynamic version is returned (it's the more recent discovery).
        """
        merged: dict[str, LoadedSkill] = {}
        for name, skill in self._skills.items():
            merged[name] = skill
        # Dynamic overrides static.
        for name, skill in self._dynamic.items():
            merged[name] = skill
        return list(merged.values())

    def model_invocable(self) -> list[LoadedSkill]:
        """Skills the LLM can invoke via SkillTool.

        Excludes ``disable_model_invocation=True``.
        """
        return [s for s in self.all_skills() if not s.manifest.disable_model_invocation]

    def user_invocable(self) -> list[LoadedSkill]:
        """Skills the user can invoke via ``/skill-name``."""
        return [s for s in self.all_skills() if s.manifest.user_invocable]

    def conditional_count(self) -> int:
        """Number of skills waiting for file-operation activation."""
        return len(self._conditional)

    def conditional_skills(self) -> dict[str, LoadedSkill]:
        """Direct access to the conditional pool (for activate_conditional)."""
        return self._conditional

    # ── Lifecycle ────────────────────────────────────────────────

    def clear(self) -> None:
        """Drop all pools."""
        self._skills.clear()
        self._conditional.clear()
        self._dynamic.clear()

    def clear_dynamic(self) -> None:
        """Drop only dynamic skills (for testing)."""
        self._dynamic.clear()


__all__ = ["SkillRegistry"]
