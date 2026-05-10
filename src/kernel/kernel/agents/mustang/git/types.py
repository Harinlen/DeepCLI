"""Shared types for the Git subsystem.

Groups the data classes used by GitManager, WorktreeStore, and the
worktree tools.  No behaviour — plain dataclasses / Pydantic models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Git context snapshot — injected into system prompt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitContext:
    """Session-start git snapshot injected into the system prompt.

    CC reference: ``context.ts:36-111`` — memoized, truncated, snapshot
    semantics (not live).
    """

    branch: str
    main_branch: str
    user: str
    status: str  # truncated to MAX_STATUS_CHARS
    recent_commits: str  # last 5 oneline commits

    def format(self) -> str:
        """Format as the CC-style ``gitStatus`` block."""
        return "\n".join(
            [
                "gitStatus: This is the git status at the start of the"
                " conversation. Note that this status is a snapshot in time,"
                " and will not update during the conversation.",
                "",
                f"Current branch: {self.branch}",
                f"Main branch (you will usually use this for PRs): {self.main_branch}",
                f"Git user: {self.user}",
                "",
                "Status:",
                self.status or "(clean)",
                "",
                "Recent commits:",
                self.recent_commits or "(no commits)",
            ]
        )


# ---------------------------------------------------------------------------
# Worktree session tracking
# ---------------------------------------------------------------------------


@dataclass
class WorktreeSession:
    """Tracks an active worktree entered via EnterWorktreeTool.

    Persisted to SQLite (WorktreeStore) for crash-recovery GC.
    """

    session_id: str
    original_cwd: Path
    worktree_path: Path
    worktree_branch: str
    slug: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class GitConfig(BaseModel):
    """User-configurable git settings (``config.yaml: git:`` section)."""

    binary: str | None = Field(
        None,
        description="Path to git binary.  If unset, searches PATH.",
    )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GitTimeoutError(Exception):
    """Raised when a git subprocess exceeds its timeout."""


__all__ = [
    "GitConfig",
    "GitContext",
    "GitTimeoutError",
    "WorktreeSession",
]
