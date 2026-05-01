"""Skill model — Markdown + YAML frontmatter single-file format.

A skill is a ``.md`` file with YAML frontmatter (between ``---``
delimiters) and a Markdown body that serves as a prompt template.

Design decision D12: single-file format is simpler than a directory,
supports ``$ARGUMENTS`` placeholder substitution, and enables lazy
loading (frontmatter read at discovery, body loaded on demand).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Frontmatter delimiter
_DELIMITER = "---"


class Skill(BaseModel):
    """A single skill definition parsed from a Markdown file.

    Attributes:
        name: Unique skill identifier (from frontmatter).
        description: Human-readable description (shown to LLM).
        when_to_use: Hint for the LLM on when to invoke this skill.
        model: Optional model override when this skill is active.
        arguments: Argument spec string, e.g. ``"message:string"``.
        source_path: Absolute path to the ``.md`` file.
        body: The Markdown prompt template. ``None`` until loaded
              via :func:`~daemon.extensions.skills.loader.load_skill_body`.
    """

    name: str
    description: str
    when_to_use: str | None = None
    model: str | None = None
    arguments: str | None = None
    source_path: Path
    body: str | None = Field(default=None, exclude=True)


def parse_skill_file(path: Path) -> Skill | None:
    """Parse a skill ``.md`` file, reading only the frontmatter.

    The body is **not** loaded (lazy loading).  Call
    :func:`~daemon.extensions.skills.loader.load_skill_body` to
    populate it later.

    Args:
        path: Absolute path to the ``.md`` file.

    Returns:
        A ``Skill`` instance with ``body=None``, or ``None`` if
        parsing fails.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Cannot read skill file: %s", path)
        return None

    frontmatter, _ = _split_frontmatter(text)
    if frontmatter is None:
        logger.warning("No YAML frontmatter found in %s", path)
        return None

    try:
        meta = yaml.safe_load(frontmatter)
    except yaml.YAMLError:
        logger.warning("Invalid YAML frontmatter in %s", path)
        return None

    if not isinstance(meta, dict):
        logger.warning("Frontmatter is not a YAML mapping in %s", path)
        return None

    name = meta.get("name")
    description = meta.get("description")
    if not name or not description:
        logger.warning("Skill %s missing required 'name' or 'description'", path)
        return None

    return Skill(
        name=name,
        description=description,
        when_to_use=meta.get("whenToUse"),
        model=meta.get("model"),
        arguments=meta.get("arguments"),
        source_path=path.resolve(),
    )


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split a Markdown file into frontmatter and body.

    Expects the file to start with ``---``, followed by YAML, then
    another ``---``, then the body.

    Returns:
        ``(frontmatter_str, body_str)`` — frontmatter is ``None`` if
        delimiters are missing.
    """
    stripped = text.lstrip()
    if not stripped.startswith(_DELIMITER):
        return None, text

    # Find the closing delimiter
    after_first = stripped[len(_DELIMITER) :]
    closing_idx = after_first.find(f"\n{_DELIMITER}")
    if closing_idx == -1:
        return None, text

    frontmatter = after_first[:closing_idx].strip()
    # Body starts after the closing delimiter line
    rest = after_first[closing_idx + len(f"\n{_DELIMITER}") :]
    # Skip the rest of the closing delimiter line
    newline_idx = rest.find("\n")
    if newline_idx != -1:
        body = rest[newline_idx + 1 :]
    else:
        body = ""

    return frontmatter, body


def render_skill_body(skill: Skill, arguments: str = "") -> str:
    """Render a skill's body with argument substitution.

    Replaces ``$ARGUMENTS`` in the body with the provided arguments
    string.

    Args:
        skill: The skill (must have ``body`` loaded).
        arguments: User-provided arguments to substitute.

    Returns:
        The rendered prompt text.

    Raises:
        ValueError: If the skill body has not been loaded.
    """
    if skill.body is None:
        raise ValueError(f"Skill '{skill.name}' body not loaded — call load_skill_body() first")
    return skill.body.replace("$ARGUMENTS", arguments)
