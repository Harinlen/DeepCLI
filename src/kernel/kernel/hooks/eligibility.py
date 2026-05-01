"""Per-hook eligibility checks.

Runs once per hook at load time.  A hook that fails any of the
checks is skipped silently (info-level log) — not an error, just a
"this machine doesn't satisfy your requirements" decision.

Inspired by OpenClaw's ``shouldIncludeHook`` pipeline but pared down
to the three signals mustang actually needs: OS, binaries on PATH,
and environment variables present.
"""

from __future__ import annotations

import os
import shutil
import sys

from kernel.hooks.manifest import HookManifest


def is_eligible(manifest: HookManifest) -> tuple[bool, str | None]:
    """Decide whether ``manifest`` is loadable on this machine.

    Returns ``(True, None)`` when all predicates pass.  When a check
    fails, returns ``(False, reason)`` so the loader can include the
    reason in its skip log — surfacing to the operator why their hook
    silently disappeared.
    """
    if manifest.os and sys.platform not in manifest.os:
        return False, f"os {sys.platform!r} not in allow-list {list(manifest.os)}"

    for binary in manifest.requires.bins:
        if shutil.which(binary) is None:
            return False, f"required binary not on PATH: {binary!r}"

    for var in manifest.requires.env:
        # Both unset and empty-string count as "not satisfied"; the
        # latter usually indicates an accidentally-empty .env entry.
        if not os.environ.get(var):
            return False, f"required env var unset or empty: {var!r}"

    return True, None


__all__ = ["is_eligible"]
