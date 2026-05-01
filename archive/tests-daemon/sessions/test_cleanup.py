"""Tests for session auto-expiry and cleanup."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daemon.config.schema import SessionsRuntimeConfig
from daemon.sessions.cleanup import cleanup_expired_sessions
from daemon.sessions.storage import SessionMeta


def _make_config(
    max_age_days: int = 30,
    max_count: int = 200,
    max_file_mb: int = 50,
) -> SessionsRuntimeConfig:
    return SessionsRuntimeConfig(
        max_age_days=max_age_days,
        max_count=max_count,
        max_file_mb=max_file_mb,
    )


def _create_session(
    session_dir: Path,
    session_id: str,
    updated_at: datetime | None = None,
    jsonl_size: int = 100,
) -> None:
    """Create fake session files on disk."""
    session_dir.mkdir(parents=True, exist_ok=True)

    ts = (updated_at or datetime.now(timezone.utc)).isoformat()
    meta = SessionMeta(
        session_id=session_id,
        created_at=ts,
        updated_at=ts,
        cwd="/tmp",
        model="test",
        provider="local",
    )
    meta_path = session_dir / f"{session_id}.meta.json"
    meta_path.write_text(json.dumps(meta.model_dump(), indent=2))

    jsonl_path = session_dir / f"{session_id}.jsonl"
    jsonl_path.write_text("x" * jsonl_size)


class TestAgeBasedCleanup:
    def test_deletes_old_sessions(self, tmp_path: Path) -> None:
        """Sessions older than max_age_days are deleted."""
        old_time = datetime.now(timezone.utc) - timedelta(days=40)
        _create_session(tmp_path, "old-session-1", updated_at=old_time)
        _create_session(tmp_path, "recent-session", updated_at=datetime.now(timezone.utc))

        deleted = cleanup_expired_sessions(tmp_path, _make_config(max_age_days=30))
        assert deleted == 1
        assert not (tmp_path / "old-session-1.jsonl").exists()
        assert (tmp_path / "recent-session.jsonl").exists()

    def test_skips_active_sessions(self, tmp_path: Path) -> None:
        """Active sessions are never deleted even if expired."""
        old_time = datetime.now(timezone.utc) - timedelta(days=40)
        _create_session(tmp_path, "active-old", updated_at=old_time)

        deleted = cleanup_expired_sessions(
            tmp_path, _make_config(max_age_days=30), active_session_ids={"active-old"}
        )
        assert deleted == 0
        assert (tmp_path / "active-old.jsonl").exists()

    def test_no_sessions(self, tmp_path: Path) -> None:
        """Empty directory produces no errors."""
        deleted = cleanup_expired_sessions(tmp_path, _make_config())
        assert deleted == 0


class TestCountBasedCleanup:
    def test_prunes_oldest_when_over_limit(self, tmp_path: Path) -> None:
        """Excess sessions beyond max_count are deleted oldest-first."""
        for i in range(5):
            ts = datetime.now(timezone.utc) - timedelta(days=5 - i)
            _create_session(tmp_path, f"session-{i}", updated_at=ts)

        deleted = cleanup_expired_sessions(tmp_path, _make_config(max_count=3))
        assert deleted == 2
        # The two oldest should be gone.
        assert not (tmp_path / "session-0.jsonl").exists()
        assert not (tmp_path / "session-1.jsonl").exists()
        # The three newest should remain.
        assert (tmp_path / "session-2.jsonl").exists()
        assert (tmp_path / "session-3.jsonl").exists()
        assert (tmp_path / "session-4.jsonl").exists()

    def test_skips_active_during_count_prune(self, tmp_path: Path) -> None:
        """Active session at the head of the oldest list stops pruning."""
        for i in range(4):
            ts = datetime.now(timezone.utc) - timedelta(days=4 - i)
            _create_session(tmp_path, f"session-{i}", updated_at=ts)

        # session-0 is oldest but active.
        deleted = cleanup_expired_sessions(
            tmp_path, _make_config(max_count=2), active_session_ids={"session-0"}
        )
        # Can't prune session-0 (active), stops there.
        assert deleted == 0


class TestSizeWarning:
    def test_large_file_not_deleted(self, tmp_path: Path) -> None:
        """Oversized sessions get a warning log but are NOT deleted."""
        # Create a session with a large JSONL.
        _create_session(tmp_path, "big-session", jsonl_size=60 * 1024 * 1024)

        deleted = cleanup_expired_sessions(tmp_path, _make_config(max_file_mb=50))
        assert deleted == 0
        assert (tmp_path / "big-session.jsonl").exists()


class TestCombined:
    def test_age_then_count(self, tmp_path: Path) -> None:
        """Age cleanup runs first, then count-based on the remainder."""
        # 3 old (> 10 days) + 3 recent
        for i in range(3):
            ts = datetime.now(timezone.utc) - timedelta(days=15 - i)
            _create_session(tmp_path, f"old-{i}", updated_at=ts)
        for i in range(3):
            ts = datetime.now(timezone.utc) - timedelta(days=3 - i)
            _create_session(tmp_path, f"new-{i}", updated_at=ts)

        # Age limit 10 days → delete 3 old; count limit 2 → delete 1 of remaining 3
        deleted = cleanup_expired_sessions(tmp_path, _make_config(max_age_days=10, max_count=2))
        assert deleted == 4  # 3 aged out + 1 count-pruned
