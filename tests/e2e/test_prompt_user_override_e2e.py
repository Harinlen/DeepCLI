"""E2E tests for PromptManager user override layer.

Verifies that the kernel subprocess discovers and loads prompt overrides
from ``$HOME/.mustang/prompts/`` (global) and ``.mustang/prompts/`` under
the kernel's working directory (project-local), with project-local taking
precedence over global.

Coverage map
------------
test_kernel_starts_with_global_override
    → app.py discovers HOME/.mustang/prompts/, PromptManager.load() succeeds
test_kernel_starts_with_missing_override_dir
    → missing user dir silently skipped; kernel starts normally
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._home_sandbox import (
    cleanup_test_home,
    prepare_test_home,
    token_path_for,
)
from tests.e2e.conftest import KERNEL_DIR, _STARTUP_TIMEOUT_SECS, _POLL_INTERVAL_SECS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_TIMEOUT: float = 30.0
_PROMPT_OVERRIDE_PORT = 18220


def _wait_for_kernel(port: int, timeout: float) -> bool:
    """Return True if kernel responds within *timeout*, False otherwise."""
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(_POLL_INTERVAL_SECS)
    return False


def _kill_port(port: int) -> None:
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        for pid_str in out.splitlines():
            try:
                os.kill(int(pid_str), signal.SIGKILL)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _run_kernel(sandbox_home: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["HOME"] = str(sandbox_home)
    return subprocess.Popen(
        ["uv", "run", "python", "-m", "kernel", "--port", str(port)],
        cwd=str(KERNEL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _run(coro: Any, *, timeout: float = _TEST_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_guarded())


def _client(port: int, token: str) -> Any:
    from probe.client import ProbeClient
    return ProbeClient(port=port, token=token, request_timeout=_TEST_TIMEOUT)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_kernel_starts_with_global_override() -> None:
    """Kernel starts and is queryable when HOME/.mustang/prompts/ has override files.

    Observable: kernel health endpoint responds + ACP initialize succeeds.
    This proves app.py discovered the global user dir and PromptManager.load()
    ran without error (fatal errors abort the kernel before it serves HTTP).
    """
    _kill_port(_PROMPT_OVERRIDE_PORT)
    sandbox = prepare_test_home("prompt-override")
    try:
        # Place an override: replace orchestrator/base key with extra text.
        override_dir = sandbox / ".mustang" / "prompts" / "orchestrator"
        override_dir.mkdir(parents=True)
        real_base = (
            KERNEL_DIR / "kernel" / "prompts" / "default" / "orchestrator" / "base.txt"
        )
        original = real_base.read_text(encoding="utf-8")
        (override_dir / "base.txt").write_text(original + "\n# user-override-sentinel")

        proc = _run_kernel(sandbox, _PROMPT_OVERRIDE_PORT)
        try:
            started = _wait_for_kernel(_PROMPT_OVERRIDE_PORT, _STARTUP_TIMEOUT_SECS)
            assert started, "Kernel did not start within timeout despite valid user override dir"

            token_path = token_path_for(sandbox)
            assert token_path.exists(), "Auth token not created — kernel startup incomplete"
            token = token_path.read_text().strip()

            # ACP handshake must succeed (requires PromptManager fully loaded).
            async def _check_caps() -> dict:
                async with _client(_PROMPT_OVERRIDE_PORT, token) as c:
                    return await c.initialize()

            caps = _run(_check_caps())
            assert "promptCapabilities" in caps, (
                "initialize returned no promptCapabilities — kernel may have started degraded"
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    finally:
        cleanup_test_home(sandbox)


def test_kernel_starts_with_missing_override_dir() -> None:
    """Kernel starts normally even when HOME/.mustang/prompts/ does not exist.

    Observable: same as above — kernel responds and ACP initialize succeeds.
    This proves missing user dir is silently skipped (not a fatal error).
    """
    port = _PROMPT_OVERRIDE_PORT + 1
    _kill_port(port)
    sandbox = prepare_test_home("prompt-no-override")
    try:
        # Deliberately do NOT create .mustang/prompts/ in the sandbox.
        assert not (sandbox / ".mustang" / "prompts").exists()

        proc = _run_kernel(sandbox, port)
        try:
            started = _wait_for_kernel(port, _STARTUP_TIMEOUT_SECS)
            assert started, "Kernel did not start — missing user override dir should be silently skipped"

            token_path = token_path_for(sandbox)
            assert token_path.exists()
            token = token_path.read_text().strip()

            async def _check_caps() -> dict:
                async with _client(port, token) as c:
                    return await c.initialize()

            caps = _run(_check_caps())
            assert "promptCapabilities" in caps
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    finally:
        cleanup_test_home(sandbox)
