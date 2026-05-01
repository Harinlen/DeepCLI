"""Tests for kernel.git.context — build_git_context()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kernel.git.context import MAX_STATUS_CHARS, build_git_context
from kernel.git.types import GitContext


def _make_git_mgr(
    *,
    branch: str | None = "main",
    main_branch: str | None = "origin/main",
    status: str | None = "",
    log: str | None = "abc1234 init",
    user: str | None = "Alice",
) -> AsyncMock:
    """Build a mock GitManager whose run_ok returns controlled values."""
    mgr = AsyncMock()
    responses = [branch, main_branch, status, log, user]

    async def _run_ok(args: list[str], cwd: Path, **kw) -> str | None:  # noqa: ARG001
        return responses.pop(0)

    mgr.run_ok = AsyncMock(side_effect=_run_ok)
    return mgr


class TestBuildGitContext:
    @pytest.mark.asyncio
    async def test_normal_repo(self) -> None:
        mgr = _make_git_mgr()
        ctx = await build_git_context(mgr, Path("/repo"))
        assert isinstance(ctx, GitContext)
        assert ctx.branch == "main"
        assert ctx.main_branch == "main"  # "origin/main" → "main"
        assert ctx.user == "Alice"

    @pytest.mark.asyncio
    async def test_not_git_dir(self) -> None:
        mgr = _make_git_mgr(branch=None)
        ctx = await build_git_context(mgr, Path("/tmp"))
        assert ctx is None

    @pytest.mark.asyncio
    async def test_main_branch_fallback(self) -> None:
        mgr = _make_git_mgr(main_branch=None)
        ctx = await build_git_context(mgr, Path("/repo"))
        assert ctx is not None
        assert ctx.main_branch == "main"

    @pytest.mark.asyncio
    async def test_main_branch_parse(self) -> None:
        mgr = _make_git_mgr(main_branch="origin/develop")
        ctx = await build_git_context(mgr, Path("/repo"))
        assert ctx is not None
        assert ctx.main_branch == "develop"

    @pytest.mark.asyncio
    async def test_status_truncation(self) -> None:
        long_status = "M  file.py\n" * 500  # well over 2000 chars
        mgr = _make_git_mgr(status=long_status)
        ctx = await build_git_context(mgr, Path("/repo"))
        assert ctx is not None
        assert len(ctx.status) <= MAX_STATUS_CHARS + 50  # allow for "... (truncated)"
        assert "truncated" in ctx.status

    @pytest.mark.asyncio
    async def test_user_fallback(self) -> None:
        mgr = _make_git_mgr(user=None)
        ctx = await build_git_context(mgr, Path("/repo"))
        assert ctx is not None
        assert ctx.user == "unknown"

    @pytest.mark.asyncio
    async def test_empty_status_and_log(self) -> None:
        mgr = _make_git_mgr(status=None, log=None)
        ctx = await build_git_context(mgr, Path("/repo"))
        assert ctx is not None
        assert ctx.status == ""
        assert ctx.recent_commits == ""
