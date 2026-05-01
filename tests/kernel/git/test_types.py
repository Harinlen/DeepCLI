"""Tests for kernel.git.types."""

from datetime import datetime, timezone
from pathlib import Path

from kernel.git.types import GitContext, GitTimeoutError, WorktreeSession


class TestGitContext:
    def test_format_all_fields(self) -> None:
        ctx = GitContext(
            branch="feature-x",
            main_branch="main",
            user="Alice",
            status="M  src/app.py\n?? new.txt",
            recent_commits="abc1234 fix bug\ndef5678 add feature",
        )
        text = ctx.format()
        assert "Current branch: feature-x" in text
        assert "Main branch (you will usually use this for PRs): main" in text
        assert "Git user: Alice" in text
        assert "M  src/app.py" in text
        assert "abc1234 fix bug" in text
        assert "snapshot in time" in text

    def test_format_empty_status(self) -> None:
        ctx = GitContext(
            branch="main",
            main_branch="main",
            user="Bob",
            status="",
            recent_commits="",
        )
        text = ctx.format()
        assert "(clean)" in text
        assert "(no commits)" in text

    def test_frozen(self) -> None:
        ctx = GitContext(
            branch="main",
            main_branch="main",
            user="X",
            status="",
            recent_commits="",
        )
        try:
            ctx.branch = "other"  # type: ignore[misc]
            raise AssertionError("should be frozen")
        except AttributeError:
            pass


class TestWorktreeSession:
    def test_fields(self) -> None:
        ws = WorktreeSession(
            session_id="sess-1",
            original_cwd=Path("/home/user/repo"),
            worktree_path=Path("/home/user/repo/.mustang/worktrees/feat"),
            worktree_branch="worktree-feat",
            slug="feat",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert ws.session_id == "sess-1"
        assert ws.slug == "feat"
        assert ws.worktree_path.name == "feat"


class TestGitTimeoutError:
    def test_is_exception(self) -> None:
        err = GitTimeoutError("timed out")
        assert isinstance(err, Exception)
        assert "timed out" in str(err)
