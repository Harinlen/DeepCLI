"""Git context snapshot builder.

Runs 5 parallel git commands to produce a ``GitContext`` for system
prompt injection.  CC reference: ``context.ts:36-111``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from kernel.git.types import GitContext

if TYPE_CHECKING:
    from kernel.git import GitManager

logger = logging.getLogger(__name__)

MAX_STATUS_CHARS = 2000


async def build_git_context(
    git_mgr: GitManager,
    cwd: Path,
) -> GitContext | None:
    """Build a ``GitContext`` snapshot by querying the git repo at *cwd*.

    Returns ``None`` when *cwd* is not inside a git repository (branch
    query fails).  Individual command failures are tolerated — the
    remaining fields are filled with fallback values.
    """
    branch, main_branch, status, log, user = await asyncio.gather(
        git_mgr.run_ok(["rev-parse", "--abbrev-ref", "HEAD"], cwd),
        git_mgr.run_ok(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd),
        git_mgr.run_ok(["--no-optional-locks", "status", "--short"], cwd),
        git_mgr.run_ok(["--no-optional-locks", "log", "--oneline", "-n", "5"], cwd),
        git_mgr.run_ok(["config", "user.name"], cwd),
    )

    if branch is None:
        return None  # not a git repo

    # "origin/main" → "main"
    if main_branch:
        main_branch = main_branch.rsplit("/", 1)[-1]
    else:
        main_branch = "main"

    # Truncate status to avoid prompt bloat.
    if status and len(status) > MAX_STATUS_CHARS:
        status = status[:MAX_STATUS_CHARS] + "\n... (truncated)"

    return GitContext(
        branch=branch,
        main_branch=main_branch,
        user=user or "unknown",
        status=status or "",
        recent_commits=log or "",
    )


__all__ = ["MAX_STATUS_CHARS", "build_git_context"]
