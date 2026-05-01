"""Session auto-expiry and cleanup.

Implements time-based and count-based pruning of old session files.
Runs once at daemon startup and then periodically in the background.

Cleanup strategies (all configurable via ``sessions`` config section):

- **Age-based**: Delete sessions not updated within ``max_age_days``.
- **Count-based**: When total session count exceeds ``max_count``,
  delete the oldest sessions until the count is within limits.
- **Size warning**: Sessions exceeding ``max_file_mb`` are logged as
  warnings but NOT automatically deleted (user may want to /compact).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from daemon.config.schema import SessionsRuntimeConfig
from daemon.sessions.storage import SessionMeta

logger = logging.getLogger(__name__)

# Background cleanup interval (24 hours).
_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60

# Delay before first background cleanup (10 minutes, matching Claude Code).
_INITIAL_DELAY_SECONDS = 10 * 60


def _load_all_metas(session_dir: Path) -> list[SessionMeta]:
    """Scan the session directory for all meta files.

    Returns:
        List of SessionMeta sorted by ``updated_at`` ascending (oldest first).
    """
    metas: list[SessionMeta] = []
    if not session_dir.exists():
        return metas

    for meta_path in session_dir.glob("*.meta.json"):
        try:
            meta = SessionMeta.model_validate_json(meta_path.read_text())
            metas.append(meta)
        except Exception:
            logger.warning("Skipping unreadable meta: %s", meta_path.name)

    # Sort oldest first (ascending updated_at).
    metas.sort(key=lambda m: m.updated_at)
    return metas


def cleanup_expired_sessions(
    session_dir: Path,
    config: SessionsRuntimeConfig,
    active_session_ids: set[str] | None = None,
) -> int:
    """Delete expired sessions based on age and count limits.

    Args:
        session_dir: Directory containing session files.
        config: Resolved session cleanup configuration.
        active_session_ids: IDs of sessions currently in memory
            (skipped during cleanup to avoid data loss).

    Returns:
        Number of sessions deleted.
    """
    active = active_session_ids or set()
    metas = _load_all_metas(session_dir)
    if not metas:
        return 0

    deleted = 0
    now = datetime.now(timezone.utc)

    # --- Age-based cleanup ---
    remaining: list[SessionMeta] = []
    for meta in metas:
        if meta.session_id in active:
            remaining.append(meta)
            continue

        try:
            updated = datetime.fromisoformat(meta.updated_at)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            remaining.append(meta)
            continue

        age_days = (now - updated).days
        if age_days > config.max_age_days:
            _delete_session_files(session_dir, meta.session_id)
            deleted += 1
            logger.info(
                "Expired session %s (age=%dd, limit=%dd)",
                meta.session_id[:8],
                age_days,
                config.max_age_days,
            )
        else:
            remaining.append(meta)

    # --- Count-based cleanup (oldest first) ---
    while len(remaining) > config.max_count:
        oldest = remaining[0]
        if oldest.session_id in active:
            # Active session — can't delete; stop pruning.
            break
        remaining.pop(0)
        _delete_session_files(session_dir, oldest.session_id)
        deleted += 1
        logger.info(
            "Pruned session %s (count=%d, limit=%d)",
            oldest.session_id[:8],
            len(remaining) + deleted,
            config.max_count,
        )

    # --- Size warnings (no deletion) ---
    max_bytes = config.max_file_mb * 1024 * 1024
    for meta in remaining:
        jsonl_path = session_dir / f"{meta.session_id}.jsonl"
        if jsonl_path.exists():
            size = jsonl_path.stat().st_size
            if size > max_bytes:
                logger.warning(
                    "Session %s is oversized (%.1f MB, limit %d MB). Consider running /compact.",
                    meta.session_id[:8],
                    size / 1024 / 1024,
                    config.max_file_mb,
                )

    if deleted:
        logger.info("Session cleanup complete: %d session(s) deleted", deleted)

    return deleted


def _delete_session_files(session_dir: Path, session_id: str) -> None:
    """Remove JSONL and meta files for a session."""
    for suffix in (".jsonl", ".meta.json"):
        path = session_dir / f"{session_id}{suffix}"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete %s", path)


async def start_cleanup_task(
    session_dir: Path,
    config: SessionsRuntimeConfig,
    get_active_ids: Callable[[], set[str]],
) -> asyncio.Task[None]:
    """Start the background cleanup task.

    Waits ``_INITIAL_DELAY_SECONDS`` before the first run, then
    repeats every ``_CLEANUP_INTERVAL_SECONDS``.

    Args:
        session_dir: Session storage directory.
        config: Session cleanup configuration.
        get_active_ids: Callable returning a set of active session IDs.

    Returns:
        The background asyncio task (caller should cancel on shutdown).
    """

    async def _loop() -> None:
        await asyncio.sleep(_INITIAL_DELAY_SECONDS)
        while True:
            try:
                active = get_active_ids()
                cleanup_expired_sessions(session_dir, config, active)
            except Exception:
                logger.exception("Error during session cleanup")
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)

    task = asyncio.create_task(_loop())
    return task
