"""Skill registry — name → Skill mapping with deduplication.

Skills from higher-priority sources shadow lower-priority ones with
the same name.  Deduplication uses ``source_path.resolve()`` so that
symlinks to the same file don't create duplicates.
"""

from __future__ import annotations

import logging

from daemon.extensions.skills.base import Skill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Registry of available skills.

    Skills are registered by name.  Duplicate names are silently
    skipped (first registration wins — higher-priority source).
    Duplicate paths (resolved) are also skipped.
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._seen_paths: set[str] = set()

    def register(self, skill: Skill) -> bool:
        """Register a skill.

        Args:
            skill: Skill to register.

        Returns:
            True if registered, False if skipped (duplicate name or path).
        """
        resolved = str(skill.source_path.resolve())

        # Dedup by resolved path
        if resolved in self._seen_paths:
            logger.debug("Skipping duplicate skill path: %s (%s)", skill.name, resolved)
            return False

        # Dedup by name (first wins)
        if skill.name in self._skills:
            logger.debug(
                "Skipping skill '%s' from %s — name already registered",
                skill.name,
                skill.source_path,
            )
            return False

        self._skills[skill.name] = skill
        self._seen_paths.add(resolved)
        return True

    def get(self, name: str) -> Skill | None:
        """Look up a skill by name."""
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        """Return all registered skills, sorted by name."""
        return sorted(self._skills.values(), key=lambda s: s.name)

    @property
    def skill_names(self) -> list[str]:
        """Return sorted list of registered skill names."""
        return sorted(self._skills.keys())

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills
