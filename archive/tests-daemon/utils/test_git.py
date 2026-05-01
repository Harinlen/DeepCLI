"""Tests for ``daemon.utils.git`` — git context collection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from daemon.utils.git import (
    MAX_STATUS_CHARS,
    _detect_default_branch,
    _format_git_status,
    _format_status_block,
    _run_git,
    get_git_status,
)


# ------------------------------------------------------------------
# Real-git fixtures
# ------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A temporary git repository with one commit on ``main``."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=cwd, check=True)
    (cwd / "README.md").write_text("# hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=cwd, check=True)
    return cwd


# ------------------------------------------------------------------
# _run_git
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_git_success(git_repo: Path) -> None:
    """Successful subprocess returns stripped stdout."""
    result = await _run_git(["rev-parse", "--abbrev-ref", "HEAD"], git_repo)
    assert result == "main"


@pytest.mark.asyncio
async def test_run_git_non_zero_exit(tmp_path: Path) -> None:
    """Non-zero exit returns None (tmp_path is not a git repo)."""
    result = await _run_git(["rev-parse", "--is-inside-work-tree"], tmp_path)
    assert result is None


@pytest.mark.asyncio
async def test_run_git_command_not_found(tmp_path: Path, monkeypatch) -> None:
    """Missing git binary yields None (FileNotFoundError path)."""
    import asyncio

    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await _run_git(["status"], tmp_path)
    assert result is None


@pytest.mark.asyncio
async def test_run_git_timeout(tmp_path: Path, monkeypatch) -> None:
    """Commands that exceed _CMD_TIMEOUT return None."""
    import asyncio

    class _SlowProc:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(10)
            return (b"", b"")

        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        return _SlowProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    # Patch the timeout constant to something tiny.
    import daemon.utils.git as git_mod

    monkeypatch.setattr(git_mod, "_CMD_TIMEOUT", 0.01)

    result = await _run_git(["status"], tmp_path)
    assert result is None


# ------------------------------------------------------------------
# _detect_default_branch
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_default_branch_main(git_repo: Path) -> None:
    """Repo with 'main' returns 'main'."""
    result = await _detect_default_branch(git_repo)
    assert result == "main"


@pytest.mark.asyncio
async def test_detect_default_branch_master(tmp_path: Path) -> None:
    """Repo with only 'master' returns 'master'."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=cwd, check=True)
    (cwd / "x.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=cwd, check=True)

    result = await _detect_default_branch(cwd)
    assert result == "master"


@pytest.mark.asyncio
async def test_detect_default_branch_none(tmp_path: Path) -> None:
    """Repo with neither main nor master returns None."""
    cwd = tmp_path / "repo"
    cwd.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=cwd, check=True)
    (cwd / "x.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=cwd, check=True)

    result = await _detect_default_branch(cwd)
    assert result is None


# ------------------------------------------------------------------
# _format_status_block
# ------------------------------------------------------------------


def test_format_status_block_empty() -> None:
    """No status → literal '(clean)'."""
    assert _format_status_block(None) == "(clean)"
    assert _format_status_block("") == "(clean)"


def test_format_status_block_short() -> None:
    """Short status is returned unchanged."""
    text = " M foo.py\n?? bar.py"
    assert _format_status_block(text) == text


def test_format_status_block_truncation() -> None:
    """Status beyond MAX_STATUS_CHARS is truncated with suffix."""
    long = "x" * (MAX_STATUS_CHARS + 500)
    result = _format_status_block(long)
    assert result.startswith("x" * MAX_STATUS_CHARS)
    assert "truncated" in result
    assert "Use Bash git status" in result


# ------------------------------------------------------------------
# _format_git_status
# ------------------------------------------------------------------


def test_format_git_status_full() -> None:
    """All fields present yields well-structured output."""
    result = _format_git_status(
        branch="dev/foo",
        default_branch="main",
        status=" M foo.py",
        log="abc feat: thing",
        user="Alice",
    )
    assert result is not None
    assert "snapshot in time" in result
    assert "Current branch: dev/foo" in result
    assert "Main branch" in result
    assert "main" in result
    assert "Git user: Alice" in result
    assert "Status:" in result
    assert " M foo.py" in result
    assert "Recent commits:" in result
    assert "abc feat: thing" in result


def test_format_git_status_clean_repo() -> None:
    """Empty status renders as (clean)."""
    result = _format_git_status(
        branch="main",
        default_branch="main",
        status=None,
        log="abc init",
        user=None,
    )
    assert result is not None
    assert "(clean)" in result
    # No "Git user" line when user is None.
    assert "Git user" not in result


def test_format_git_status_no_default_branch() -> None:
    """Missing default branch omits the line."""
    result = _format_git_status(
        branch="trunk",
        default_branch=None,
        status=None,
        log="abc init",
        user=None,
    )
    assert result is not None
    assert "Current branch: trunk" in result
    assert "Main branch" not in result


def test_format_git_status_all_none() -> None:
    """All core fields None returns None."""
    result = _format_git_status(None, None, None, None, None)
    assert result is None


# ------------------------------------------------------------------
# get_git_status — integration with real git
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_git_status_in_repo(git_repo: Path) -> None:
    """Real git repo yields a populated text block."""
    result = await get_git_status(git_repo)
    assert result is not None
    assert "Current branch: main" in result
    assert "Main branch" in result
    assert "Git user: Test User" in result
    assert "Recent commits:" in result
    assert "initial commit" in result


@pytest.mark.asyncio
async def test_get_git_status_not_a_git_repo(tmp_path: Path) -> None:
    """Non-git directory returns None."""
    result = await get_git_status(tmp_path)
    assert result is None


@pytest.mark.asyncio
async def test_get_git_status_with_modifications(git_repo: Path) -> None:
    """Modified files appear in the Status section."""
    (git_repo / "new.txt").write_text("hi", encoding="utf-8")
    (git_repo / "README.md").write_text("# changed\n", encoding="utf-8")

    result = await get_git_status(git_repo)
    assert result is not None
    assert "?? new.txt" in result or "new.txt" in result
    assert "README.md" in result
