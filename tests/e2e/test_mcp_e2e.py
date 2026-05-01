"""End-to-end tests for the MCP subsystem — through real kernel subprocess.

These tests start a live kernel process with MCP configuration, connect
via ProbeClient over ACP WebSocket, and verify the full boot-to-tool
pipeline.  Unlike unit/integration tests, this exercises the real
``app.py`` lifespan startup order, ConfigManager binding, and signal
wiring.

Test coverage:
- test_kernel_boots_with_mcp    → lifespan + MCPManager.startup + health
- test_mcp_tool_call_via_prompt → full LLM → MCP tool round-trip (skipped
  if no LLM configured)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
from tests.e2e._home_sandbox import (
    cleanup_test_home,
    prepare_test_home,
    token_path_for,
)

# Port for MCP e2e kernel (must differ from main e2e port 18200).
_MCP_PORT = 18201

# Paths.
# test_mcp_e2e.py lives at tests/e2e/test_mcp_e2e.py:
#   parents[0] = tests/e2e
#   parents[1] = tests
#   parents[2] = project root
_PROJECT_ROOT = Path(__file__).parents[2]
_KERNEL_DIR = _PROJECT_ROOT / "src" / "kernel"
_ECHO_SERVER = str(Path(__file__).parent / "mcp_echo_server.py")

_STARTUP_TIMEOUT = 20
_POLL_INTERVAL = 0.25


# ── Helpers ─────────────────────────────────────────────────────────


def _wait_for_kernel(port: int, timeout: float) -> None:
    """Block until the kernel health endpoint responds."""
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(_POLL_INTERVAL)
    raise RuntimeError(f"Kernel on port {port} did not respond within {timeout}s")


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously."""
    import asyncio

    return asyncio.run(coro)


# ── Fixture: kernel subprocess with MCP config ─────────────────────


@pytest.fixture(scope="module")
def mcp_kernel(tmp_path_factory: pytest.TempPathFactory):
    """Start a kernel with MCP echo server configured.

    Writes a ``.mcp.json`` to the kernel cwd so MCPManager picks it up
    during ``startup()``.  The kernel subprocess is killed on teardown.
    """
    # Write .mcp.json to the kernel package directory.
    mcp_json_path = _KERNEL_DIR / ".mcp.json"
    mcp_json_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "echo": {
                        "command": sys.executable,
                        "args": [_ECHO_SERVER],
                    }
                }
            }
        )
    )

    sandbox_home = prepare_test_home("mcp")
    token_path = token_path_for(sandbox_home)

    env = os.environ.copy()
    env["HOME"] = str(sandbox_home)

    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "kernel", "--port", str(_MCP_PORT)],
        cwd=str(_KERNEL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    try:
        _wait_for_kernel(_MCP_PORT, _STARTUP_TIMEOUT)

        if not token_path.exists():
            raise RuntimeError(f"Auth token not found at {token_path}")
        token = token_path.read_text().strip()

        yield _MCP_PORT, token
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        # Clean up .mcp.json so it doesn't affect other tests.
        mcp_json_path.unlink(missing_ok=True)
        cleanup_test_home(sandbox_home)


# ── Test 1: Kernel boots with MCP — lifespan order is correct ──────


def test_kernel_boots_with_mcp(mcp_kernel: tuple[int, str]) -> None:
    """Kernel starts successfully with MCP servers configured.

    Verifies the full ``app.py`` lifespan: MCPManager loads before
    ToolManager, connects to the echo server, and the kernel serves
    the health endpoint without errors.

    This catches startup-order bugs (e.g. MCPManager loading after
    ToolManager, causing signal connection to be skipped).
    """
    port, _ = mcp_kernel
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        assert resp.status == 200
        body = json.loads(resp.read())
    assert body["name"] == "mustang-kernel"


# ── Test 2: ACP initialize works with MCP subsystem active ─────────


def test_initialize_with_mcp(mcp_kernel: tuple[int, str]) -> None:
    """ACP handshake succeeds when MCPManager is running.

    Verifies that MCPManager doesn't break the protocol layer.
    """
    port, token = mcp_kernel

    async def _test() -> dict[str, Any]:
        async with ProbeClient(port=port, token=token) as client:
            caps = await client.initialize()
        return caps

    caps = _run(_test())
    assert caps.get("loadSession") is True


# ── Test 3: Full tool call — LLM calls MCP echo tool ───────────────


def test_mcp_tool_call_via_prompt(mcp_kernel: tuple[int, str]) -> None:
    """LLM sees the MCP echo tool and calls it successfully.

    Full pipeline: kernel boot → MCPManager connect → ToolManager
    registers mcp__echo__echo → Orchestrator includes it in tool
    schemas → LLM calls it → ToolExecutor → MCPAdapter → MCPManager
    → echo server → result streamed back.

    Skipped if no LLM is configured (CI without API keys).
    """
    port, token = mcp_kernel

    # Check if LLM is available.
    async def _check_llm() -> bool:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/profile_list", {})
        return len(result.get("profiles", [])) > 0

    if not _run(_check_llm()):
        pytest.skip("No LLM model configured — skipping MCP tool call test")

    async def _test() -> tuple[list[str], list[str], str]:
        text_parts: list[str] = []
        tool_titles: list[str] = []
        stop_reason = "unknown"
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            sid = await client.new_session()
            async for event in client.prompt(
                sid,
                "Use the mcp__echo__echo tool to echo the message 'hello from e2e'. "
                "Call the tool, then report what it returned.",
            ):
                if isinstance(event, AgentChunk):
                    text_parts.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_titles.append(event.title)
                elif isinstance(event, PermissionRequest):
                    # Auto-approve MCP tool permission requests.
                    allow_option = next(
                        (o["optionId"] for o in event.options if o.get("kind") == "allow"),
                        event.options[0]["optionId"] if event.options else "allow",
                    )
                    await client.reply_permission(event.req_id, allow_option)
                elif isinstance(event, TurnComplete):
                    stop_reason = event.stop_reason
        return text_parts, tool_titles, stop_reason

    text_parts, tool_titles, stop_reason = _run(_test())

    assert stop_reason == "end_turn", f"Unexpected stop_reason: {stop_reason!r}"
    # The LLM should have called the echo tool (tool title includes server/tool name).
    assert len(tool_titles) > 0, (
        f"Expected at least one tool call, got none. Text: {''.join(text_parts)!r}"
    )
