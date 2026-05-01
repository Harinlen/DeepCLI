"""Pytest fixtures for kernel end-to-end tests.

Starts a fresh mustang-kernel subprocess on a dedicated test port
(``TEST_PORT``), waits for it to serve the health endpoint, and tears
it down after the session.  Each test receives a ``(port, token)``
tuple it can use to construct ``ProbeClient`` instances.

Port strategy
-------------
Tests run on port ``18200`` so they never collide with a developer's
live kernel on ``8200``.

State isolation
---------------
The kernel subprocess is launched with ``HOME`` pointing at a fresh
``/tmp/mustang-e2e-kernel/`` sandbox (see ``_home_sandbox``).  All
``~/.mustang/`` paths inside the kernel resolve there, so tests never
touch the developer's real state directory.  The sandbox is wiped on
teardown and on the next startup, so a crashed test never leaves
orphan cron tasks firing against the developer's DB.

Subprocess lifecycle
--------------------
The fixture is ``scope="session"`` so the kernel starts once and is
reused across every test in the module, keeping the test suite fast.
The process is killed in ``finally`` to ensure cleanup even on test
failures.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
from collections.abc import Generator
from pathlib import Path

import pytest

from tests.e2e._home_sandbox import (
    cleanup_test_home,
    prepare_test_home,
    token_path_for,
)


async def poll_for_session_output(
    session_id: str,
    marker: str,
    *,
    timeout: float = 10.0,
    interval: float = 0.2,
) -> bool:
    """Poll task output files until *marker* appears or timeout expires.

    Scans all *.output files under /tmp/mustang/{session_id}/tasks/.
    Returns True if found within timeout, False otherwise.
    """
    task_dir = Path(tempfile.gettempdir()) / "mustang" / session_id / "tasks"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if task_dir.is_dir():
            for path in task_dir.glob("*.output"):
                try:
                    if marker in path.read_text(errors="replace"):
                        return True
                except OSError:
                    pass
        await asyncio.sleep(interval)
    return False


_E2E_GROUP_PATTERNS: list[tuple[str, list[str]]] = [
    ("core",         ["kernel", "secret", "session_resume", "prompt_user_override"]),
    ("tools",        ["bash_safety", "file_read", "todo", "tool_search",
                      "web_fetch", "task_background", "monitor",
                      "agent_tool", "ask_user_question", "send_message"]),
    ("skills",       ["skill_"]),
    ("mcp",          ["mcp"]),
    ("memory",       ["memory"]),
    ("orchestrator", ["orchestrator_hooks", "plan_mode", "repl"]),
    ("git",          ["git"]),
    ("schedule",     ["schedule"]),
]


def _e2e_group(filename: str) -> str | None:
    stem = filename.removeprefix("test_").removesuffix("_e2e.py")
    for group, patterns in _E2E_GROUP_PATTERNS:
        if any(stem == p or stem.startswith(p) for p in patterns):
            return group
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark every test in tests/e2e/ with ``e2e`` + a group marker."""
    for item in items:
        path = str(item.fspath)
        if "/e2e/" not in path:
            continue
        item.add_marker(pytest.mark.e2e)
        group = _e2e_group(Path(path).name)
        if group:
            item.add_marker(getattr(pytest.mark, group))

# Port reserved for e2e tests — must differ from the dev kernel port (8200).
TEST_PORT = 18200

# Path to the kernel package (src/kernel/).
# conftest.py lives at tests/e2e/conftest.py, so:
#   parents[0] = tests/e2e
#   parents[1] = tests
#   parents[2] = project root
_PROJECT_ROOT = Path(__file__).parents[2]
KERNEL_DIR = _PROJECT_ROOT / "src" / "kernel"

# Auth token path is derived from the sandbox HOME when the fixture
# runs — see below.  This module-level constant is kept for backward
# compatibility with tests that import it; it resolves against the
# default sandbox label used by the ``kernel`` fixture.
TOKEN_PATH = token_path_for(Path(os.path.join("/tmp", "mustang-e2e-kernel")))

# How long to wait for the kernel to become ready.
_STARTUP_TIMEOUT_SECS = 20
_POLL_INTERVAL_SECS = 0.25


def _kill_port_occupants(port: int) -> None:
    """Kill any process listening on *port* (best-effort, Linux/macOS).

    Prevents "address already in use" from a zombie kernel left by a
    previous crashed test run.
    """
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return  # nothing listening, or lsof not installed
    for pid_str in out.splitlines():
        try:
            pid = int(pid_str.strip())
            import os as _os
            if pid != _os.getpid():
                _os.kill(pid, signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError):
            pass


def _wait_for_kernel(port: int, timeout: float) -> None:
    """Block until the kernel's health endpoint replies or timeout expires.

    Raises ``RuntimeError`` if the kernel does not respond in time.
    """
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return  # Kernel is up.
        except (urllib.error.URLError, OSError):
            time.sleep(_POLL_INTERVAL_SECS)
    raise RuntimeError(
        f"Kernel on port {port} did not respond within {timeout}s. "
        "Check that `uv` is on PATH and the kernel package has no import errors."
    )


@pytest.fixture(scope="session")
def kernel() -> "Generator[tuple[int, str], None, None]":
    """Start the kernel on TEST_PORT and yield ``(port, auth_token)``.

    The kernel process is terminated in the fixture teardown.
    """


    # Kill leftover kernel from a previous crashed run.
    _kill_port_occupants(TEST_PORT)

    # Fresh sandbox HOME — wipes any leftover and rebuilds.
    sandbox_home = prepare_test_home("kernel")
    token_path = token_path_for(sandbox_home)

    # Capture stderr to a temp file so we can dump it on startup failure.
    stderr_path = _PROJECT_ROOT / ".pytest_kernel_stderr.log"
    stderr_file = stderr_path.open("w")

    env = os.environ.copy()
    env["HOME"] = str(sandbox_home)

    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "kernel", "--port", str(TEST_PORT)],
        cwd=str(KERNEL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
        env=env,
    )

    try:
        _wait_for_kernel(TEST_PORT, _STARTUP_TIMEOUT_SECS)

        # Read the auth token the kernel created (or reused) at startup.
        if not token_path.exists():
            _dump_stderr(stderr_path)
            raise RuntimeError(f"Auth token not found at {token_path} after kernel startup.")
        token = token_path.read_text().strip()

        yield TEST_PORT, token
    except Exception:
        # Dump kernel stderr on any startup failure for diagnosis.
        _dump_stderr(stderr_path)
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr_file.close()
        stderr_path.unlink(missing_ok=True)
        cleanup_test_home(sandbox_home)


def _dump_stderr(path: Path) -> None:
    """Print captured kernel stderr to pytest output for diagnostics."""
    try:
        text = path.read_text()
        if text.strip():
            print(f"\n=== Kernel stderr (captured) ===\n{text}\n=== End kernel stderr ===")
    except OSError:
        pass
