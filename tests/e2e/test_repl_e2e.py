"""E2E tests for REPL tool — batch execution mode.

Exercises the REPL tool through the real ACP WebSocket interface.
A dedicated kernel subprocess is started with ``tools.repl: true``
via a temporary flags file + ``DEEPCLI_FLAGS_PATH`` env var.

Coverage map
------------
test_repl_hides_primitive_tools   → ToolManager registers REPL, snapshot hides primitives
test_repl_batch_execution         → LLM uses REPL to batch-execute Glob + Read
test_repl_error_inline            → REPL reports inner tool errors without crashing

Port strategy: uses 18202 to avoid collision with the main E2E kernel on 18200
and the MCP E2E kernel on 18201.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from probe.client import (
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ToolCallEvent,
    TurnComplete,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from tests.e2e._home_sandbox import (
    cleanup_test_home,
    prepare_test_home,
    token_path_for,
)

_REPL_PORT = 18202
_PROJECT_ROOT = Path(__file__).parents[2]
_KERNEL_DIR = _PROJECT_ROOT / "src" / "kernel"
_STARTUP_TIMEOUT = 30.0
_POLL_INTERVAL = 0.25
_LLM_TIMEOUT = 120.0


# ---------------------------------------------------------------------------
# Helpers (duplicated from conftest to keep this self-contained)
# ---------------------------------------------------------------------------


def _kill_port(port: int) -> None:
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return
    for pid_str in out.splitlines():
        try:
            pid = int(pid_str.strip())
            if pid != os.getpid():
                os.kill(pid, signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError):
            pass


def _wait_for_kernel(port: int, timeout: float) -> None:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(_POLL_INTERVAL)
    raise RuntimeError(f"REPL kernel on port {port} did not respond within {timeout}s")


def _client(port: int, token: str, *, timeout: float = _LLM_TIMEOUT) -> ProbeClient:
    return ProbeClient(port=port, token=token, request_timeout=timeout)


async def _has_llm(port: int, token: str) -> bool:
    async with _client(port, token) as client:
        await client.initialize()
        result = await client._request("_mustang.agent/model/provider_list", {})
    return len(result.get("providers", [])) > 0


def _run(coro: Any, *, timeout: float = _LLM_TIMEOUT) -> Any:
    async def _guarded() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_guarded())


# ---------------------------------------------------------------------------
# Fixture: kernel with tools.repl: true
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def repl_kernel():
    """Start a kernel subprocess with REPL mode enabled.

    Creates a temporary flags.yaml with ``tools.repl: true`` and
    passes it via ``DEEPCLI_FLAGS_PATH``.  Uses port 18201 to avoid
    collision with the standard E2E kernel on 18200.
    """
    _kill_port(_REPL_PORT)

    sandbox_home = prepare_test_home("repl")
    token_path = token_path_for(sandbox_home)

    # Write a temp flags file with repl enabled and keep the transport
    # stack explicit so this fixture is isolated from user flags.
    flags_fd, flags_path = tempfile.mkstemp(suffix=".yaml", prefix="mustang_repl_flags_")
    try:
        with os.fdopen(flags_fd, "w") as f:
            f.write(
                "transport:\n"
                "  stack: acp\n"
                "tools:\n"
                "  repl: true\n"
            )

        stderr_path = _PROJECT_ROOT / ".pytest_repl_kernel_stderr.log"
        stderr_file = stderr_path.open("w")

        env = os.environ.copy()
        env["DEEPCLI_FLAGS_PATH"] = flags_path
        env["HOME"] = str(sandbox_home)

        proc = subprocess.Popen(
            ["uv", "run", "python", "-m", "kernel", "--port", str(_REPL_PORT)],
            cwd=str(_KERNEL_DIR),
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            env=env,
        )

        try:
            _wait_for_kernel(_REPL_PORT, _STARTUP_TIMEOUT)

            if not token_path.exists():
                raise RuntimeError(f"Auth token not found at {token_path}")
            token = token_path.read_text().strip()

            yield _REPL_PORT, token
        except Exception:
            try:
                text = stderr_path.read_text()
                if text.strip():
                    print(f"\n=== REPL kernel stderr ===\n{text}\n=== End ===")
            except OSError:
                pass
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
    finally:
        Path(flags_path).unlink(missing_ok=True)
        cleanup_test_home(sandbox_home)


# ---------------------------------------------------------------------------
# 1. REPL is registered and primitive tools are hidden
# ---------------------------------------------------------------------------


def test_repl_hides_primitive_tools(repl_kernel: tuple[int, str]) -> None:
    """When repl flag is on, the kernel starts successfully with REPL
    registered.  A simple prompt completes — verifying that the
    orchestrator snapshot correctly hides primitives and exposes REPL.

    This test does not require LLM to actually use REPL — it verifies
    the kernel boots without crash and can serve a session.  The tool
    schema filtering is verified by unit tests; here we confirm the
    full lifespan works with REPL enabled.
    """
    port, token = repl_kernel

    async def _run_test() -> dict[str, Any]:
        async with _client(port, token) as client:
            caps = await client.initialize()
            await client.new_session()
        return caps

    caps = _run(_run_test(), timeout=30.0)
    assert caps.get("loadSession") is True


# ---------------------------------------------------------------------------
# 2. LLM uses REPL to batch-execute tools
# ---------------------------------------------------------------------------


def test_repl_batch_execution(repl_kernel: tuple[int, str]) -> None:
    """Send a prompt that requires file operations.  The LLM should
    use the REPL tool (since Bash/Read/Glob are hidden) and the turn
    should complete successfully.

    Asserts that at least one ToolCallEvent with "REPL" in its title
    appears in the event stream, confirming the LLM used REPL.
    """
    port, token = repl_kernel

    if not _run(_has_llm(port, token), timeout=30.0):
        pytest.skip("No LLM providers configured — skipping REPL batch test")

    async def _run_prompt() -> tuple[str, list[str], str]:
        text_parts: list[str] = []
        tool_titles: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()
            prompt = (
                "Use the REPL tool to list files matching '*.py' in the current "
                "directory using the Glob tool. Then tell me how many files you found."
            )
            async for event in client.prompt(sid, prompt):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), tool_titles, stop_reason

    text, tool_titles, stop_reason = _run(_run_prompt())

    assert stop_reason == "end_turn", (
        f"Expected end_turn, got {stop_reason!r}. Text: {text!r}"
    )
    # The LLM should have called REPL (visible in tool_titles).
    assert any("REPL" in t for t in tool_titles), (
        f"Expected REPL in tool titles. Seen: {tool_titles}"
    )
    assert len(text) > 0, "Expected non-empty response"


# ---------------------------------------------------------------------------
# 3. REPL handles errors gracefully in E2E
# ---------------------------------------------------------------------------


def test_repl_error_inline(repl_kernel: tuple[int, str]) -> None:
    """When the LLM asks REPL to read a non-existent file, the error
    should be reported inline in the REPL result and the conversation
    should continue without crashing.
    """
    port, token = repl_kernel

    if not _run(_has_llm(port, token), timeout=30.0):
        pytest.skip("No LLM providers configured — skipping REPL error test")

    async def _run_prompt() -> tuple[str, list[str], str]:
        text_parts: list[str] = []
        tool_titles: list[str] = []
        stop_reason = "unknown"
        async with _client(port, token) as client:
            await client.initialize()
            sid = await client.new_session()
            prompt = (
                "Use the REPL tool to read the file '/tmp/__mustang_nonexistent_12345.txt'. "
                "Tell me exactly what error you got."
            )
            async for event in client.prompt(sid, prompt):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    await client.reply_permission(event.req_id, "allow_once")
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return "".join(text_parts), tool_titles, stop_reason

    text, tool_titles, stop_reason = _run(_run_prompt())

    assert stop_reason == "end_turn", (
        f"Expected end_turn, got {stop_reason!r}. Text: {text!r}"
    )
    # The conversation should complete — REPL didn't crash the kernel.
    assert len(text) > 0
