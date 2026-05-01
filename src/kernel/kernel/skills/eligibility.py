"""Per-skill eligibility checks.

Two-phase filtering:

1. **Static eligibility** (``is_eligible``) — runs once at startup /
   discovery time.  Checks OS, binaries on PATH, and environment
   variables.  Mirrors ``hooks/eligibility.py``.

2. **Dynamic visibility** (``is_visible``) — runs at listing time
   (each ``get_skill_listing`` call) because the tool set can change
   mid-session (MCP servers connecting / disconnecting).  Checks
   ``requires.tools``, ``requires.toolsets``, and ``fallback_for``.

A skill that fails static eligibility is never loaded.  A skill that
fails dynamic visibility is hidden from the listing but stays in the
registry — it becomes visible again if the required tools appear.
"""

from __future__ import annotations

import os
import shutil
import sys

from kernel.skills.types import LoadedSkill, SkillManifest


def is_eligible(manifest: SkillManifest) -> tuple[bool, str | None]:
    """Decide whether *manifest* is loadable on this machine.

    Returns ``(True, None)`` when all predicates pass.  On failure
    returns ``(False, reason)`` so the loader can include the reason
    in its skip log.

    Checks (all must pass):
    - ``os``: ``sys.platform`` must be in the allow-list (empty = any).
    - ``requires.bins``: every binary must resolve via ``shutil.which``.
    - ``requires.env``: every variable must be set and non-empty.
    """
    if manifest.os and sys.platform not in manifest.os:
        return False, f"os {sys.platform!r} not in allow-list {list(manifest.os)}"

    for binary in manifest.requires.bins:
        if shutil.which(binary) is None:
            return False, f"required binary not on PATH: {binary!r}"

    for var in manifest.requires.env:
        if not os.environ.get(var):
            return False, f"required env var unset or empty: {var!r}"

    return True, None


def is_visible(skill: LoadedSkill, available_tools: set[str]) -> bool:
    """Decide whether *skill* should appear in the current listing.

    Called at listing time — the tool set may have changed since
    startup (MCP servers, dynamic tool registration).

    Checks:
    - ``requires.tools``: all listed tools must be in *available_tools*.
    - ``requires.toolsets``: all listed toolsets must be available.
    - ``fallback_for``: if **all** primary tools/toolsets are available,
      this fallback skill is hidden (the primary is better).
    """
    req = skill.manifest.requires

    # requires.tools — any missing → hide
    if req.tools and not all(t in available_tools for t in req.tools):
        return False

    # requires.toolsets — any missing → hide
    if req.toolsets and not all(ts in available_tools for ts in req.toolsets):
        return False

    # fallback_for — all primaries present → hide this fallback
    fb = skill.manifest.fallback_for
    if fb is not None:
        tools_satisfied = not fb.tools or all(t in available_tools for t in fb.tools)
        toolsets_satisfied = not fb.toolsets or all(ts in available_tools for ts in fb.toolsets)
        if tools_satisfied and toolsets_satisfied:
            return False

    return True


__all__ = ["is_eligible", "is_visible"]
