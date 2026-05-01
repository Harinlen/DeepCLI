"""Worktree CRUD operations.

All functions accept a ``GitManager`` as the first argument so that
git commands route through the configured binary with consistent
timeout / error handling.

CC reference: ``src/utils/worktree.ts``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from kernel.tools.types import ToolInputError

if TYPE_CHECKING:
    from kernel.git import GitManager
    from kernel.git.types import WorktreeSession


# ---------------------------------------------------------------------------
# Slug validation (CC aligned: worktree.ts validateWorktreeSlug)
# ---------------------------------------------------------------------------

_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def validate_slug(slug: str) -> None:
    """Validate a worktree slug.

    Rules (CC-aligned):
    - 1–64 characters
    - Each ``/``-separated segment matches ``[a-zA-Z0-9._-]+``
    - No ``.`` or ``..`` segments (path traversal protection)
    """
    if not slug or len(slug) > 64:
        raise ToolInputError("slug must be 1-64 characters")
    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise ToolInputError("slug must not contain '.' or '..' segments")
        if not _SEGMENT_RE.match(segment):
            raise ToolInputError(
                f"slug segment '{segment}' contains invalid characters (allowed: a-z A-Z 0-9 . _ -)"
            )


# ---------------------------------------------------------------------------
# Git root resolution
# ---------------------------------------------------------------------------


async def find_git_root(git_mgr: GitManager, cwd: Path) -> Path:
    """Find the git root for *cwd*, resolving through nested worktrees.

    If *cwd* is already inside a worktree, traces back to the main
    repository root via ``git rev-parse --git-common-dir``.
    """
    toplevel = await git_mgr.run_ok(["rev-parse", "--show-toplevel"], cwd)
    if toplevel is None:
        raise ToolInputError("not in a git repository")

    root = Path(toplevel)
    git_dir = root / ".git"
    if git_dir.is_file():
        # .git is a file → currently inside a worktree; resolve to main repo.
        common = await git_mgr.run_ok(["rev-parse", "--git-common-dir"], cwd)
        if common:
            return Path(common).resolve().parent
    return root


# ---------------------------------------------------------------------------
# Worktree create / remove
# ---------------------------------------------------------------------------


async def create_worktree(
    git_mgr: GitManager,
    git_root: Path,
    slug: str,
) -> tuple[Path, str]:
    """Create a git worktree under ``.mustang/worktrees/<slug>/``.

    Returns ``(worktree_path, branch_name)``.  Supports fast-resume
    when the worktree directory already exists and is valid.
    """
    worktree_dir = git_root / ".mustang" / "worktrees" / slug
    branch_name = f"worktree-{slug}"

    # Fast resume — already exists and valid.
    if worktree_dir.exists() and (worktree_dir / ".git").is_file():
        branch = await git_mgr.run_ok(["rev-parse", "--abbrev-ref", "HEAD"], worktree_dir)
        return worktree_dir, branch or branch_name

    # Ensure parent directory exists.
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # Resolve base commit.
    base = await git_mgr.run_ok(["rev-parse", "HEAD"], git_root)
    if base is None:
        raise ToolInputError("cannot determine HEAD — is this an empty repository?")

    rc, _, stderr = await git_mgr.run(
        ["worktree", "add", "-B", branch_name, str(worktree_dir), base],
        cwd=git_root,
    )
    if rc != 0:
        raise ToolInputError(f"git worktree add failed: {stderr.strip()}")

    return worktree_dir, branch_name


async def setup_sparse_checkout(
    git_mgr: GitManager,
    worktree_path: Path,
    paths: list[str],
) -> None:
    """Enable sparse-checkout in *worktree_path* for *paths*.

    CC reference: ``worktree.ts:336-366``.
    """
    rc, _, stderr = await git_mgr.run(["sparse-checkout", "init", "--cone"], worktree_path)
    if rc != 0:
        raise ToolInputError(f"sparse-checkout init failed: {stderr.strip()}")

    rc, _, stderr = await git_mgr.run(["sparse-checkout", "set", *paths], worktree_path)
    if rc != 0:
        raise ToolInputError(f"sparse-checkout set failed: {stderr.strip()}")


async def count_changes(git_mgr: GitManager, worktree_path: Path) -> int:
    """Count uncommitted + untracked changes in *worktree_path*."""
    output = await git_mgr.run_ok(["--no-optional-locks", "status", "--porcelain"], worktree_path)
    if output is None:
        return 0
    return len([line for line in output.splitlines() if line.strip()])


async def remove_worktree(
    git_mgr: GitManager,
    ws: WorktreeSession,
) -> None:
    """Remove a git worktree and delete its branch (best-effort)."""
    await git_mgr.run(
        ["worktree", "remove", "--force", str(ws.worktree_path)],
        cwd=ws.original_cwd,
    )
    # Branch delete is best-effort — may already be gone.
    await git_mgr.run_ok(["branch", "-D", ws.worktree_branch], ws.original_cwd)


__all__ = [
    "count_changes",
    "create_worktree",
    "find_git_root",
    "remove_worktree",
    "setup_sparse_checkout",
    "validate_slug",
]
