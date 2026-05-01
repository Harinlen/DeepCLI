"""Disk snapshot cache for skill manifests (Hermes pattern).

Accelerates cold startup by caching parsed frontmatter metadata to
``~/.mustang/.skills_snapshot.json``.  On startup, the snapshot is
validated against each SKILL.md's ``mtime`` + ``size`` — if all
match, the snapshot is used directly (skipping YAML parsing).  Any
mismatch triggers a full rescan + snapshot rewrite.

Two-layer caching:
- Layer 1: in-memory ``SkillRegistry`` (primary, always warm after startup).
- Layer 2: disk snapshot (accelerates the first startup layer).
"""

from __future__ import annotations

import orjson
import logging
from pathlib import Path
from typing import Any

from kernel.skills.types import (
    LoadedSkill,
)

logger = logging.getLogger(__name__)

_SNAPSHOT_VERSION = 1
_DEFAULT_SNAPSHOT_PATH = Path.home() / ".mustang" / ".skills_snapshot.json"


def load_snapshot(
    snapshot_path: Path | None = None,
) -> list[dict[str, Any]] | None:
    """Load and validate the snapshot file.

    Returns the skill entries list, or ``None`` if the snapshot is
    missing, corrupt, or has a version mismatch.
    """
    path = snapshot_path or _DEFAULT_SNAPSHOT_PATH
    if not path.is_file():
        return None

    try:
        data = orjson.loads(path.read_text(encoding="utf-8"))
    except (orjson.JSONDecodeError, OSError) as exc:
        logger.debug("skills snapshot unreadable: %s", exc)
        return None

    if not isinstance(data, dict):
        return None
    if data.get("version") != _SNAPSHOT_VERSION:
        logger.debug("skills snapshot version mismatch")
        return None

    manifest = data.get("manifest")
    skills = data.get("skills")
    if not isinstance(manifest, dict) or not isinstance(skills, list):
        return None

    return skills


def validate_snapshot(
    snapshot_skills: list[dict[str, Any]],
    base_dirs: list[Path],
) -> bool:
    """Check whether every SKILL.md in the snapshot still has the
    same mtime and size on disk.

    Returns ``True`` if the snapshot is fully valid.
    """
    # Build a lookup of expected file stats from the snapshot.
    expected: dict[str, tuple[int, int]] = {}
    for entry in snapshot_skills:
        rel_path = entry.get("rel_path")
        stats = entry.get("stats")
        if rel_path and isinstance(stats, list) and len(stats) == 2:
            expected[rel_path] = (int(stats[0]), int(stats[1]))

    if not expected:
        return False

    # Check each expected file exists with matching stats.
    for rel_path, (exp_mtime_ns, exp_size) in expected.items():
        found = False
        for base in base_dirs:
            full = base / rel_path
            if full.is_file():
                try:
                    st = full.stat()
                    if st.st_mtime_ns == exp_mtime_ns and st.st_size == exp_size:
                        found = True
                        break
                except OSError:
                    pass
        if not found:
            return False

    return True


def write_snapshot(
    skills: list[LoadedSkill],
    base_dirs: list[Path],
    snapshot_path: Path | None = None,
) -> None:
    """Write the snapshot file from the current skill set."""
    path = snapshot_path or _DEFAULT_SNAPSHOT_PATH
    entries: list[dict[str, Any]] = []
    manifest_map: dict[str, list[int]] = {}

    for skill in skills:
        # Compute relative path from any base dir.
        rel_path: str | None = None
        for base in base_dirs:
            try:
                rel_path = str(skill.file_path.relative_to(base))
                break
            except ValueError:
                continue

        if rel_path is None:
            rel_path = str(skill.file_path)

        # File stats for validation.
        try:
            st = skill.file_path.stat()
            manifest_map[rel_path] = [st.st_mtime_ns, st.st_size]
        except OSError:
            manifest_map[rel_path] = [0, 0]

        entries.append(
            {
                "name": skill.manifest.name,
                "description": skill.manifest.description,
                "source": skill.source.value,
                "layer_priority": skill.layer_priority,
                "rel_path": rel_path,
                "stats": manifest_map.get(rel_path, [0, 0]),
                "user_invocable": skill.manifest.user_invocable,
                "disable_model_invocation": skill.manifest.disable_model_invocation,
                "when_to_use": skill.manifest.when_to_use,
            }
        )

    payload = {
        "version": _SNAPSHOT_VERSION,
        "manifest": manifest_map,
        "skills": entries,
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
        logger.debug("skills snapshot written: %d skills", len(entries))
    except OSError as exc:
        logger.debug("failed to write skills snapshot: %s", exc)


__all__ = ["load_snapshot", "validate_snapshot", "write_snapshot"]
