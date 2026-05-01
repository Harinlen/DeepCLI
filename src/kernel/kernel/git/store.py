"""WorktreeStore — SQLite persistence for worktree session tracking.

Shares ``kernel.db`` with CronStore (``kernel/schedule/store.py``).
Used for crash-recovery GC at startup and session-resume cwd restore.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from kernel.git.types import WorktreeSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS worktrees (
    session_id      TEXT PRIMARY KEY,
    slug            TEXT NOT NULL,
    worktree_path   TEXT NOT NULL,
    original_cwd    TEXT NOT NULL,
    worktree_branch TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""


class WorktreeStore:
    """WorktreeSession SQLite persistence layer."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the database and ensure schema exists."""
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.commit()
        logger.info("WorktreeStore opened: %s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("WorktreeStore not opened")
        return self._db

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def insert(self, ws: WorktreeSession) -> None:
        """Persist a worktree session record."""
        await self.db.execute(
            """INSERT OR REPLACE INTO worktrees
               (session_id, slug, worktree_path, original_cwd,
                worktree_branch, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                ws.session_id,
                ws.slug,
                str(ws.worktree_path),
                str(ws.original_cwd),
                ws.worktree_branch,
                ws.created_at.isoformat(),
            ),
        )
        await self.db.commit()

    async def delete(self, session_id: str) -> None:
        """Remove a worktree session record."""
        await self.db.execute(
            "DELETE FROM worktrees WHERE session_id = ?",
            (session_id,),
        )
        await self.db.commit()

    async def list_all(self) -> list[WorktreeSession]:
        """Return all worktree session records (startup GC)."""
        cursor = await self.db.execute("SELECT * FROM worktrees")
        rows = await cursor.fetchall()
        return [_row_to_ws(row) for row in rows]

    async def get_by_session(self, session_id: str) -> WorktreeSession | None:
        """Look up a single record by session_id (primary key)."""
        cursor = await self.db.execute(
            "SELECT * FROM worktrees WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return _row_to_ws(row) if row is not None else None


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------


def _row_to_ws(row: aiosqlite.Row) -> WorktreeSession:
    return WorktreeSession(
        session_id=row["session_id"],
        slug=row["slug"],
        worktree_path=Path(row["worktree_path"]),
        original_cwd=Path(row["original_cwd"]),
        worktree_branch=row["worktree_branch"],
        created_at=datetime.fromisoformat(row["created_at"]).replace(tzinfo=timezone.utc),
    )


__all__ = ["WorktreeStore"]
