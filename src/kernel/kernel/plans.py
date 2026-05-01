"""Plan file management — persistent plan storage for plan mode.

Aligned with Claude Code's ``src/utils/plans.ts``.  Each session gets
a unique plan file identified by a word-slug (e.g. ``jazzy-wandering-hoare.md``).
Plans are stored under ``~/.mustang/plans/`` and can also be persisted
to the session event log for recovery when the filesystem is unreliable.
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path

logger = logging.getLogger(__name__)

# Word lists for slug generation (adjective + noun, CC-style).
_ADJECTIVES = [
    "amber",
    "bold",
    "calm",
    "dark",
    "eager",
    "fair",
    "gentle",
    "happy",
    "icy",
    "jade",
    "keen",
    "lush",
    "mild",
    "neat",
    "odd",
    "pale",
    "quick",
    "red",
    "shy",
    "tame",
    "ultra",
    "vast",
    "warm",
    "young",
    "zen",
    "azure",
    "brisk",
    "clear",
    "deep",
    "epic",
    "fresh",
    "grand",
    "hazy",
    "iron",
    "jazzy",
    "kind",
    "lean",
    "merry",
    "noble",
    "open",
    "prime",
    "raw",
    "sage",
    "tidy",
    "vivid",
    "wise",
    "crisp",
    "dusty",
]

_NOUNS = [
    "arch",
    "beam",
    "cave",
    "dawn",
    "edge",
    "fern",
    "gate",
    "helm",
    "isle",
    "jade",
    "knot",
    "lake",
    "mesa",
    "node",
    "oak",
    "peak",
    "quay",
    "reef",
    "star",
    "tide",
    "vale",
    "wave",
    "yard",
    "zone",
    "arc",
    "bolt",
    "cove",
    "dusk",
    "elm",
    "ford",
    "glen",
    "haze",
    "ice",
    "jet",
    "kite",
    "loom",
    "mist",
    "nest",
    "opal",
    "pine",
    "rain",
    "sage",
    "thorn",
    "vine",
    "wren",
    "birch",
    "cliff",
    "delta",
]

_MAX_SLUG_RETRIES = 10

# Module-level slug cache: session_id → slug string.
_slug_cache: dict[str, str] = {}

# Default plans directory.
_DEFAULT_PLANS_DIR = Path.home() / ".mustang" / "plans"


def get_plans_directory() -> Path:
    """Return the plans directory, creating it if necessary.

    Default: ``~/.mustang/plans/``.  Can be overridden by setting
    the ``MUSTANG_PLANS_DIR`` environment variable.
    """
    env_override = os.environ.get("MUSTANG_PLANS_DIR")
    plans_dir = Path(env_override) if env_override else _DEFAULT_PLANS_DIR
    try:
        plans_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("failed to create plans directory %s", plans_dir, exc_info=True)
    return plans_dir


def _generate_slug() -> str:
    """Generate a random two-word slug like ``jazzy-peak``."""
    adj = random.choice(_ADJECTIVES)  # noqa: S311
    noun = random.choice(_NOUNS)  # noqa: S311
    return f"{adj}-{noun}"


def get_plan_slug(session_id: str) -> str:
    """Return (or generate) the word-slug for a session's plan file.

    The slug is cached per session_id for the lifetime of this process.
    Retries up to ``_MAX_SLUG_RETRIES`` times to avoid filename collisions
    with existing plan files.
    """
    cached = _slug_cache.get(session_id)
    if cached is not None:
        return cached

    plans_dir = get_plans_directory()
    slug = _generate_slug()
    for _ in range(_MAX_SLUG_RETRIES):
        candidate = plans_dir / f"{slug}.md"
        if not candidate.exists():
            break
        slug = _generate_slug()

    _slug_cache[session_id] = slug
    return slug


def get_plan_file_path(session_id: str, agent_id: str | None = None) -> Path:
    """Return the plan file path for a session (or sub-agent).

    Main session: ``{plans_dir}/{slug}.md``
    Sub-agent:    ``{plans_dir}/{slug}-agent-{agent_id}.md``
    """
    slug = get_plan_slug(session_id)
    plans_dir = get_plans_directory()
    if agent_id:
        return plans_dir / f"{slug}-agent-{agent_id}.md"
    return plans_dir / f"{slug}.md"


def get_plan(session_id: str, agent_id: str | None = None) -> str | None:
    """Read the plan file content, or ``None`` if it doesn't exist."""
    path = get_plan_file_path(session_id, agent_id)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("failed to read plan file %s", path, exc_info=True)
        return None


def is_session_plan_file(path: Path | str, session_id: str) -> bool:
    """Check if *path* is a plan file belonging to *session_id*.

    Matches both main and agent-specific plan files.  Path is normalized
    to prevent traversal bypasses via ``..`` segments.

    Security: uses ``os.path.normpath`` + prefix check, same as CC's
    ``isSessionPlanFile()`` in ``filesystem.ts``.
    """
    normalized = os.path.normpath(str(path))
    slug = get_plan_slug(session_id)
    plans_dir = get_plans_directory()
    expected_prefix = str(plans_dir / slug)
    return normalized.startswith(expected_prefix) and normalized.endswith(".md")


def clear_slug_cache(session_id: str | None = None) -> None:
    """Clear cached slugs.  Useful for testing."""
    if session_id is None:
        _slug_cache.clear()
    else:
        _slug_cache.pop(session_id, None)


__all__ = [
    "clear_slug_cache",
    "get_plan",
    "get_plan_file_path",
    "get_plan_slug",
    "get_plans_directory",
    "is_session_plan_file",
]
