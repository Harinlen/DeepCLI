"""Project-level configuration discovery and loading.

Supports three user-accessible config layers:

1. **User** — ``~/.mustang/config.yaml`` (global, existing).
2. **Project** — ``<root>/.mustang/settings.json`` (git-tracked, team-shared).
3. **Local** — ``<root>/.mustang/settings.local.json`` (auto-gitignored, personal).

Merge precedence: local > project > user.

Security boundary: project/local configs may only contain safe fields
(permissions, hooks, mcp_servers, tools, skills).  Provider credentials
and daemon network settings are **never** loaded from project config.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_DIR_NAME = ".mustang"
PROJECT_CONFIG_NAME = "settings.json"
LOCAL_CONFIG_NAME = "settings.local.json"

# Fields that may NOT appear in project/local config — security boundary.
_DISALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "provider",
        "daemon",
        "default_provider",
    }
)

# Fields that use union semantics (concatenate + deduplicate) during merge.
_UNION_FIELDS: frozenset[str] = frozenset(
    {
        "allow",
        "deny",
    }
)


def find_project_root(cwd: Path) -> Path | None:
    """Walk from *cwd* up to filesystem root looking for ``.mustang/``.

    Args:
        cwd: Starting directory (typically the session's working dir).

    Returns:
        Directory containing ``.mustang/``, or ``None``.
    """
    for parent in [cwd, *cwd.parents]:
        if (parent / PROJECT_DIR_NAME).is_dir():
            return parent
    return None


def load_project_settings(root: Path) -> tuple[dict, dict]:
    """Load project + local settings from *root*.

    Strips disallowed fields with a warning.

    Args:
        root: Project root (the directory containing ``.mustang/``).

    Returns:
        ``(project_overrides, local_overrides)`` — both may be empty dicts.
    """
    project = _load_json(root / PROJECT_DIR_NAME / PROJECT_CONFIG_NAME)
    local = _load_json(root / PROJECT_DIR_NAME / LOCAL_CONFIG_NAME)
    project = _strip_disallowed(project, "project")
    local = _strip_disallowed(local, "local")
    return project, local


def merge_configs(
    user: dict,
    project: dict,
    local: dict,
) -> dict:
    """Merge three config layers with correct precedence.

    Merge rules:
    - Scalars: later layer overwrites (local > project > user).
    - Dicts: deep merge recursively.
    - Lists: concatenate + deduplicate for known union fields
      (permissions.allow, permissions.deny); later layer overwrites
      for all other lists.

    Args:
        user: User-level config (from ``~/.mustang/config.yaml``).
        project: Project-level overrides.
        local: Local-level overrides.

    Returns:
        Merged config dict ready for ``SourceConfig.model_validate()``.
    """
    merged = _deep_merge(user, project)
    merged = _deep_merge(merged, local)
    return merged


def ensure_local_gitignored(root: Path) -> None:
    """Append ``settings.local.json`` to ``.gitignore`` if not present.

    Idempotent — safe to call multiple times.  Fire-and-forget.

    Args:
        root: Project root directory.
    """
    gitignore = root / ".gitignore"
    pattern = f"{PROJECT_DIR_NAME}/{LOCAL_CONFIG_NAME}"

    try:
        if gitignore.exists() and pattern in gitignore.read_text():
            return
        with gitignore.open("a") as f:
            f.write(f"\n# Mustang local config (personal overrides)\n{pattern}\n")
        logger.debug("Added %s to .gitignore", pattern)
    except OSError:
        logger.debug("Could not update .gitignore at %s", gitignore)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _stable_repr(item: Any) -> str:
    """Produce a deterministic string for deduplication of config items."""
    if isinstance(item, dict):
        return json.dumps(item, sort_keys=True, default=str)
    return str(item)


def _load_json(path: Path) -> dict:
    """Load a JSON file, returning an empty dict if missing or invalid."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning("Config at %s is not a JSON object — ignoring", path)
            return {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cannot load config at %s: %s", path, exc)
        return {}


def _strip_disallowed(data: dict, source_label: str) -> dict:
    """Remove disallowed top-level keys and log warnings."""
    for key in list(data):
        if key in _DISALLOWED_FIELDS:
            logger.warning(
                "Ignoring disallowed field %r in %s config (security boundary)",
                key,
                source_label,
            )
            del data[key]
    return data


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (override wins).

    - Dicts: recursive merge.
    - Lists under known union keys: concatenate + deduplicate.
    - Everything else: override replaces base.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif key in _UNION_FIELDS and isinstance(result.get(key), list) and isinstance(value, list):
            # Union semantics: concatenate and deduplicate.
            combined = list(result[key])
            seen = set(_stable_repr(item) for item in combined)
            for item in value:
                r = _stable_repr(item)
                if r not in seen:
                    combined.append(item)
                    seen.add(r)
            result[key] = combined
        else:
            result[key] = value
    return result
