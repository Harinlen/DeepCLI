"""Skill discovery + deduplication.

Walks the configured source layers, parses each candidate skill
directory's ``SKILL.md``, runs eligibility filtering, and returns
:class:`LoadedSkill` records.

A skill that fails to load (malformed manifest, eligibility failure)
is logged and skipped.  One bad skill never prevents the others from
loading — mirrors HookManager's per-entry try/catch policy.

Supports recursive scanning to be compatible with:
- Claude Code flat structure: ``skill-name/SKILL.md``
- Hermes category structure: ``category/skill-name/SKILL.md``
- OpenClaw flat structure: ``skill-name/SKILL.md``
"""

from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path

from kernel.skills.eligibility import is_eligible
from kernel.skills.manifest import ManifestError, parse_skill_manifest
from kernel.skills.types import LoadedSkill, SkillSource

logger = logging.getLogger(__name__)

_SKILL_FILENAME = "SKILL.md"


# ------------------------------------------------------------------
# Public API — startup discovery
# ------------------------------------------------------------------


def discover(
    *,
    project_dir: Path | None,
    project_compat_dir: Path | None,
    external_dirs: list[Path],
    user_dir: Path,
    user_compat_dir: Path | None,
    bundled_skills: list[LoadedSkill],
) -> tuple[list[LoadedSkill], list[LoadedSkill]]:
    """Multi-layer discovery.  Returns ``(unconditional, conditional)``.

    ``conditional`` = skills with ``paths`` frontmatter that wait for
    file-operation activation.

    Scan order (highest priority first):
    ``project → project-compat → external → user → user-compat → bundled``

    Within the same priority level, ``.mustang/skills/`` is scanned
    before ``.claude/skills/`` so it wins on name collisions.
    """
    all_skills: list[LoadedSkill] = []

    # Project layer (priority 0)
    if project_dir is not None:
        all_skills.extend(_discover_layer(project_dir, SkillSource.PROJECT, priority=0))
    if project_compat_dir is not None:
        all_skills.extend(_discover_layer(project_compat_dir, SkillSource.PROJECT, priority=0))

    # External dirs (priority 1)
    for ext_dir in external_dirs:
        all_skills.extend(_discover_layer(ext_dir, SkillSource.EXTERNAL, priority=1))

    # User layer (priority 2)
    all_skills.extend(_discover_layer(user_dir, SkillSource.USER, priority=2))
    if user_compat_dir is not None:
        all_skills.extend(_discover_layer(user_compat_dir, SkillSource.USER, priority=2))

    # Bundled (priority 3)
    all_skills.extend(bundled_skills)

    # Deduplicate by resolved path (handles symlinks).
    deduped = _dedup(all_skills)

    # Split into unconditional and conditional.
    unconditional: list[LoadedSkill] = []
    conditional: list[LoadedSkill] = []
    for skill in deduped:
        if skill.manifest.paths:
            conditional.append(skill)
        else:
            unconditional.append(skill)

    return unconditional, conditional


# ------------------------------------------------------------------
# Public API — dynamic discovery (runtime)
# ------------------------------------------------------------------


def discover_for_paths(
    file_paths: list[str],
    cwd: str,
    known_dirs: set[str],
    claude_compat: bool = True,
) -> list[Path]:
    """Walk up from *file_paths* to *cwd*, looking for skill directories.

    CWD-level skills are already loaded at startup — this only
    discovers **nested** ``.mustang/skills/`` (and ``.claude/skills/``
    when *claude_compat* is True) directories.

    *known_dirs* tracks already-checked paths (hit or miss) to avoid
    redundant ``stat`` calls across multiple file operations.

    Returns newly discovered skill directories, sorted deepest-first
    (closer to the file = higher precedence).
    """
    resolved_cwd = os.path.normpath(cwd)
    sep = os.sep
    new_dirs: list[Path] = []

    skill_subdirs = [".mustang/skills"]
    if claude_compat:
        skill_subdirs.append(".claude/skills")

    for file_path in file_paths:
        current_dir = os.path.dirname(os.path.abspath(file_path))

        # Walk up to cwd but NOT including cwd itself.
        while current_dir.startswith(resolved_cwd + sep):
            for subdir in skill_subdirs:
                skill_dir = os.path.join(current_dir, subdir)
                if skill_dir in known_dirs:
                    continue
                known_dirs.add(skill_dir)
                if os.path.isdir(skill_dir):
                    new_dirs.append(Path(skill_dir))

            parent = os.path.dirname(current_dir)
            if parent == current_dir:
                break
            current_dir = parent

    # Deepest first (most specific).
    return sorted(new_dirs, key=lambda p: len(p.parts), reverse=True)


def activate_conditional(
    file_paths: list[str],
    cwd: str,
    conditional_pool: dict[str, LoadedSkill],
) -> list[LoadedSkill]:
    """Check conditional skills' ``paths`` globs against *file_paths*.

    When a skill's glob pattern matches, it's removed from the pool
    and returned.  Uses ``fnmatch`` for gitignore-style matching.

    Returns list of newly activated skills.
    """
    if not conditional_pool:
        return []

    activated: list[LoadedSkill] = []

    for name in list(conditional_pool):
        skill = conditional_pool[name]
        patterns = skill.manifest.paths
        if not patterns:
            continue

        for file_path in file_paths:
            rel_path = os.path.relpath(file_path, cwd)
            # Skip paths outside cwd.
            if rel_path.startswith(".."):
                continue

            for pattern in patterns:
                if fnmatch.fnmatch(rel_path, pattern):
                    conditional_pool.pop(name)
                    activated.append(skill)
                    logger.info(
                        "skills: activated conditional skill %r (matched %r)",
                        name,
                        rel_path,
                    )
                    break
            else:
                continue
            break  # This skill is activated, move to next.

    return activated


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _discover_layer(
    base_dir: Path,
    source: SkillSource,
    priority: int,
) -> list[LoadedSkill]:
    """Recursively scan *base_dir* for ``SKILL.md`` files.

    Supports both flat (``skill-name/SKILL.md``) and nested
    (``category/skill-name/SKILL.md``) layouts.  The skill name is
    always the **parent directory** of ``SKILL.md``.
    """
    if not base_dir.is_dir():
        return []

    out: list[LoadedSkill] = []
    try:
        for skill_md in sorted(base_dir.rglob(_SKILL_FILENAME)):
            skill_dir = skill_md.parent
            loaded = _try_load_skill(skill_dir, skill_md, source, priority)
            if loaded is not None:
                out.append(loaded)
    except OSError as exc:
        logger.warning("skills: failed to scan %s — %s", base_dir, exc)

    return out


def _try_load_skill(
    skill_dir: Path,
    skill_md: Path,
    source: SkillSource,
    priority: int,
) -> LoadedSkill | None:
    """Parse + eligibility-check a single skill directory.

    Returns ``None`` and logs the reason when any step fails.
    """
    # 1. Parse manifest.
    try:
        manifest = parse_skill_manifest(skill_dir)
    except ManifestError as exc:
        logger.warning("skills: skipping %s — %s", skill_dir.name, exc)
        return None

    # 2. Eligibility (OS / bins / env).
    eligible, reason = is_eligible(manifest)
    if not eligible:
        logger.info("skills: skipping %s — %s", manifest.name, reason)
        return None

    logger.debug(
        "skills: discovered %s [%s] from %s",
        manifest.name,
        source.value,
        skill_dir,
    )
    return LoadedSkill(
        manifest=manifest,
        source=source,
        layer_priority=priority,
        file_path=skill_md,
    )


def _dedup(skills: list[LoadedSkill]) -> list[LoadedSkill]:
    """Deduplicate by resolved path (handles symlinks).

    First occurrence wins (scan order = priority order).
    Also deduplicates by name — earlier layers take precedence.
    """
    seen_paths: dict[str, SkillSource] = {}
    seen_names: dict[str, SkillSource] = {}
    result: list[LoadedSkill] = []

    for skill in skills:
        # Path-based dedup (symlinks).
        try:
            real = str(skill.file_path.resolve())
        except OSError:
            real = str(skill.file_path)

        if real in seen_paths:
            logger.debug(
                "skills: dedup %s by path (already from %s)",
                skill.manifest.name,
                seen_paths[real].value,
            )
            continue
        seen_paths[real] = skill.source

        # Name-based dedup (same name in different layers).
        name = skill.manifest.name
        if name in seen_names:
            logger.debug(
                "skills: dedup %s by name (already from %s)",
                name,
                seen_names[name].value,
            )
            continue
        seen_names[name] = skill.source

        result.append(skill)

    dedup_count = len(skills) - len(result)
    if dedup_count > 0:
        logger.debug("skills: deduplicated %d skills", dedup_count)

    return result


__all__ = ["activate_conditional", "discover", "discover_for_paths"]
