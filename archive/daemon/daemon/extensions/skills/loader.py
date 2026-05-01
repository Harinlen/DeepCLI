"""Skill discovery — scan directories for skill ``.md`` files.

Searches multiple sources in priority order (project → user → bundled).
Only reads YAML frontmatter at discovery time; the body is loaded on
demand via :func:`load_skill_body`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from daemon.extensions.skills.base import Skill, _split_frontmatter, parse_skill_file

logger = logging.getLogger(__name__)


def discover_skills(
    skill_dirs: list[Path],
) -> list[Skill]:
    """Discover skills from multiple directories.

    Scans each directory for ``*.md`` files and parses their
    frontmatter.  Directories are searched in priority order — skills
    from earlier directories take precedence over later ones with the
    same name.  Deduplication by ``name`` is handled by the caller
    (SkillRegistry).

    Args:
        skill_dirs: Directories to scan, in priority order
                    (highest first).

    Returns:
        List of Skill instances (body not loaded).
    """
    skills: list[Skill] = []

    for skill_dir in skill_dirs:
        if not skill_dir.is_dir():
            logger.debug("Skill directory does not exist: %s", skill_dir)
            continue

        md_files = sorted(skill_dir.glob("*.md"))
        for md_file in md_files:
            # Skip hidden files
            if md_file.name.startswith("."):
                continue

            skill = parse_skill_file(md_file)
            if skill is not None:
                skills.append(skill)
                logger.debug("Discovered skill '%s' from %s", skill.name, md_file)

    return skills


def load_skill_body(skill: Skill) -> str:
    """Load the body (prompt template) of a skill from disk.

    Reads the full file, splits off the frontmatter, and caches
    the body on the Skill instance.

    Args:
        skill: A skill whose ``body`` is ``None``.

    Returns:
        The body text.

    Raises:
        OSError: If the file cannot be read.
    """
    if skill.body is not None:
        return skill.body

    text = skill.source_path.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    skill.body = body.strip()
    return skill.body
