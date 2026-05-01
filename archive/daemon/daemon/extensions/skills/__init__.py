"""Skill subsystem — Markdown + YAML frontmatter skill files."""

from daemon.extensions.skills.base import Skill
from daemon.extensions.skills.loader import discover_skills, load_skill_body
from daemon.extensions.skills.registry import SkillRegistry

__all__ = ["Skill", "SkillRegistry", "discover_skills", "load_skill_body"]
