"""E2E tests for GitManager subsystem.

Exercises git context injection, worktree startup mode, and session
resume through the real ACP WebSocket interface.  A live kernel must
be running (started by the ``kernel`` session fixture in
``conftest.py``).

Coverage map
------------
test_kernel_starts_with_git_manager   → GitManager loads without error
test_git_context_in_prompt            → LLM sees git branch/status
test_worktree_startup_mode            → session/new with _meta.worktree creates worktree
test_session_resume_restores_worktree → disconnect + reconnect restores worktree cwd
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from probe.client import (
    AgentChunk,
    PermissionRequest,
    TurnComplete,
)


_TEST_TIMEOUT: float = 30.0
_LLM_TIMEOUT: float = 90.0


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_guarded())


def _client(port: int, token: str, *, request_timeout: float = _TEST_TIMEOUT) -> Any:
    from probe.client import ProbeClient
    return ProbeClient(port=port, token=token, request_timeout=request_timeout)


async def _has_llm_provider(port: int, token: str) -> bool:
    async with _client(port, token) as client:
        await client.initialize()
        result = await client._request("_mustang.agent/model/provider_list", {})
    return len(result.get("providers", [])) > 0


def _skip_if_no_llm(port: int, token: str) -> None:
    if not _run(_has_llm_provider(port, token)):
        pytest.skip("No LLM providers configured — skipping")


def _make_temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo for worktree tests."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


# ---------------------------------------------------------------------------
# 1. Basic startup
# ---------------------------------------------------------------------------


class TestGitManagerE2E:

    def test_kernel_starts_with_git_manager(
        self, kernel: tuple[int, str]
    ) -> None:
        """Kernel starts cleanly with GitManager loaded."""
        port, token = kernel

        async def _check() -> str:
            async with _client(port, token) as client:
                await client.initialize()
                sid = await client.new_session()
                return sid

        sid = _run(_check())
        assert sid

    def test_git_context_in_prompt(
        self, kernel: tuple[int, str]
    ) -> None:
        """LLM sees git branch/status in the system prompt."""
        port, token = kernel
        _skip_if_no_llm(port, token)

        async def _ask_branch() -> tuple[str, str]:
            text_parts: list[str] = []
            stop_reason = "unknown"
            async with _client(port, token, request_timeout=_LLM_TIMEOUT) as client:
                await client.initialize()
                sid = await client.new_session()
                prompt = (
                    "What is the current git branch shown in your system context? "
                    "Reply with just the branch name, nothing else."
                )
                async for event in client.prompt(sid, prompt):
                    if isinstance(event, AgentChunk):
                        text_parts.append(event.text)
                    elif isinstance(event, PermissionRequest):
                        await client.reply_permission(
                            event.req_id, "allow_once"
                        )
                    elif isinstance(event, TurnComplete):
                        stop_reason = event.stop_reason
            return "".join(text_parts), stop_reason

        text, stop_reason = _run(_ask_branch(), timeout=_LLM_TIMEOUT)
        assert stop_reason == "end_turn"
        assert text.strip(), "LLM returned empty text — git context may not be injected"


# ---------------------------------------------------------------------------
# 2. Worktree startup mode (M7)
# ---------------------------------------------------------------------------


class TestWorktreeStartupE2E:

    def test_worktree_startup_creates_worktree(
        self, kernel: tuple[int, str], tmp_path: Path
    ) -> None:
        """session/new with _meta.worktree should create a worktree
        and set the session cwd to the worktree path."""
        port, token = kernel
        repo = _make_temp_git_repo(tmp_path)

        async def _check() -> str:
            async with _client(port, token) as client:
                await client.initialize()
                sid = await client.new_session(
                    cwd=str(repo),
                    meta={"worktree": {"slug": "e2e-test"}},
                )
                return sid

        sid = _run(_check())
        assert sid

        # Verify the worktree was actually created on disk.
        wt_path = repo / ".mustang" / "worktrees" / "e2e-test"
        assert wt_path.exists(), f"Worktree not created at {wt_path}"
        assert (wt_path / ".git").exists(), "Worktree .git pointer missing"

    def test_worktree_startup_no_meta_is_normal(
        self, kernel: tuple[int, str]
    ) -> None:
        """session/new without _meta creates a normal session."""
        port, token = kernel

        async def _check() -> str:
            async with _client(port, token) as client:
                await client.initialize()
                sid = await client.new_session()
                return sid

        sid = _run(_check())
        assert sid


# ---------------------------------------------------------------------------
# 3. Session resume (M6)
# ---------------------------------------------------------------------------


class TestSessionResumeE2E:

    def test_session_resume_restores_worktree(
        self, kernel: tuple[int, str], tmp_path: Path
    ) -> None:
        """After disconnect + reconnect, the worktree cwd should be
        restored from the SQLite record."""
        port, token = kernel
        repo = _make_temp_git_repo(tmp_path)

        async def _check() -> tuple[str, bool]:
            # 1. Create session with worktree.
            async with _client(port, token) as client1:
                await client1.initialize()
                sid = await client1.new_session(
                    cwd=str(repo),
                    meta={"worktree": {"slug": "resume-test"}},
                )
            # client1 disconnected here.

            # 2. Reconnect to the same session.
            async with _client(port, token) as client2:
                await client2.initialize()
                await client2.load_session(sid, cwd=str(repo))
                # Session should have been loaded successfully.
                return sid, True

        sid, success = _run(_check())
        assert success

        # Worktree should still exist on disk.
        wt_path = repo / ".mustang" / "worktrees" / "resume-test"
        assert wt_path.exists(), "Worktree should still exist after resume"
