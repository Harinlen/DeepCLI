"""Bundled skills — shipped with the kernel binary.

Bundled skills are registered programmatically at import time, not
discovered from the filesystem.  They follow the same ``LoadedSkill``
interface as file-based skills but have their content compiled in.

When a bundled skill declares ``files``, those files are extracted to
``~/.mustang/bundled-skills/<name>/`` on first invocation (lazy), and
the skill body is prefixed with
``Base directory for this skill: <dir>`` so the LLM can ``Read``
supporting files.

Aligned with Claude Code's ``bundledSkills.ts``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from kernel.skills.types import (
    LoadedSkill,
    SkillManifest,
    SkillSource,
)

logger = logging.getLogger(__name__)

_BUNDLED_SKILLS_ROOT = Path.home() / ".mustang" / "bundled-skills"


@dataclass
class BundledSkillDef:
    """Definition for a skill that ships with the kernel."""

    name: str
    description: str
    when_to_use: str | None = None
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    context: str | None = None  # "fork" or None
    agent: str | None = None
    model: str | None = None
    files: dict[str, str] | None = None
    get_prompt: Callable[[str], Awaitable[str] | str] | None = None
    body: str = ""


# Internal registry — populated at import time by register_bundled_skill().
_bundled_registry: list[LoadedSkill] = []


def register_bundled_skill(definition: BundledSkillDef) -> LoadedSkill:
    """Register a bundled skill.  Called at module import time.

    Returns the ``LoadedSkill`` for testing convenience.
    """
    extract_dir = _BUNDLED_SKILLS_ROOT / definition.name

    manifest = SkillManifest(
        name=definition.name,
        description=definition.description,
        has_user_specified_description=True,
        allowed_tools=definition.allowed_tools,
        argument_hint=definition.argument_hint,
        when_to_use=definition.when_to_use,
        user_invocable=definition.user_invocable,
        disable_model_invocation=definition.disable_model_invocation,
        context="fork" if definition.context == "fork" else None,
        agent=definition.agent,
        model=definition.model,
        base_dir=extract_dir if definition.files else Path("/dev/null"),
    )

    body = definition.body
    if definition.files:
        body = f"Base directory for this skill: {extract_dir}\n\n{body}"

    skill = LoadedSkill(
        manifest=manifest,
        source=SkillSource.BUNDLED,
        layer_priority=3,
        file_path=extract_dir / "SKILL.md",  # Virtual path.
        _body=body,
    )
    _bundled_registry.append(skill)
    return skill


def get_bundled_skills() -> list[LoadedSkill]:
    """Return all registered bundled skills."""
    return list(_bundled_registry)


def clear_bundled_skills() -> None:
    """Clear the registry (for testing)."""
    _bundled_registry.clear()


def extract_bundled_files(name: str, files: dict[str, str]) -> Path | None:
    """Extract bundled skill files to disk on first invocation.

    Returns the extraction directory, or None on failure.
    """
    target_dir = _BUNDLED_SKILLS_ROOT / name
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for rel_path, content in files.items():
            file_path = target_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        return target_dir
    except OSError as exc:
        logger.warning("Failed to extract bundled skill %r: %s", name, exc)
        return None


# Import bundled skill modules to trigger registration at import time.
import kernel.skills.bundled.loop_skill as _loop_skill  # noqa: F401, E402


__all__ = [
    "BundledSkillDef",
    "clear_bundled_skills",
    "extract_bundled_files",
    "get_bundled_skills",
    "register_bundled_skill",
]
