"""E2E tests for ListMcpResources + ReadMcpResource tools.

Closure seams verified:
  Seam 1: OrchestratorDeps.mcp = _mcp_manager
           SessionManager wires MCPManager into OrchestratorDeps so
           it reaches ToolExecutor._build_tool_context.
  Seam 2: ToolContext.mcp_manager = deps.mcp
           ToolExecutor forward-passes MCPManager to tools via ToolContext;
           ListMcpResourcesTool and ReadMcpResourceTool call it directly.

Both seams are exercised by:
  1. Booting a real kernel subprocess with mcp_resources_server.py configured.
  2. Asking the LLM (via ProbeClient) to call each tool.
  3. Verifying the tool was called and returned data from the real server.

Skipped when no LLM is configured.
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

_PORT = 18204
_PROJECT_ROOT = Path(__file__).parents[2]
_KERNEL_DIR = _PROJECT_ROOT / "src" / "kernel"
_RESOURCES_SERVER = str(Path(__file__).parent / "mcp_resources_server.py")
_STARTUP_TIMEOUT = 30
_POLL_INTERVAL = 0.25


# ── helpers ──────────────────────────────────────────────────────────────────


def _wait_for_kernel(port: int, timeout: float) -> None:
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
    import asyncio
    return asyncio.run(coro)


# ── fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def resources_kernel(tmp_path_factory: pytest.TempPathFactory):
    """Start a kernel subprocess with mcp_resources_server configured."""
    mcp_json_path = _KERNEL_DIR / ".mcp.json"
    mcp_json_path.write_text(
        json.dumps({
            "mcpServers": {
                "resources": {
                    "command": sys.executable,
                    "args": [_RESOURCES_SERVER],
                }
            }
        })
    )

    sandbox_home = prepare_test_home("mcp-resources")
    env = os.environ.copy()
    env["HOME"] = str(sandbox_home)

    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "kernel", "--port", str(_PORT)],
        cwd=str(_KERNEL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    try:
        _wait_for_kernel(_PORT, _STARTUP_TIMEOUT)
        token_path = token_path_for(sandbox_home)
        if not token_path.exists():
            raise RuntimeError(f"Auth token not found at {token_path}")
        token = token_path.read_text().strip()
        yield _PORT, token
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        mcp_json_path.unlink(missing_ok=True)
        cleanup_test_home(sandbox_home)


# ── LLM guard ────────────────────────────────────────────────────────────────


def _llm_available(port: int, token: str) -> bool:
    async def _check() -> bool:
        async with ProbeClient(port=port, token=token) as client:
            await client.initialize()
            result = await client._request("_mustang.agent/model/profile_list", {})
        return len(result.get("profiles", [])) > 0
    return _run(_check())


# ── helpers for running a prompt turn ────────────────────────────────────────


async def _run_prompt(
    port: int,
    token: str,
    prompt: str,
) -> tuple[str, list[str]]:
    """Run one prompt turn; return (full_text, tool_titles)."""
    text_parts: list[str] = []
    tool_titles: list[str] = []

    async with ProbeClient(port=port, token=token) as client:
        await client.initialize()
        sid = await client.new_session()
        async for event in client.prompt(sid, prompt):
            if isinstance(event, AgentChunk):
                text_parts.append(event.text)
            elif isinstance(event, ToolCallEvent):
                tool_titles.append(event.title)
            elif isinstance(event, PermissionRequest):
                # Auto-approve all tool calls.
                allow_id = next(
                    (o["optionId"] for o in event.options if o.get("kind") == "allow"),
                    event.options[0]["optionId"] if event.options else "allow",
                )
                await client.reply_permission(event.req_id, allow_id)
            elif isinstance(event, TurnComplete):
                if event.error:
                    raise event.error

    return "".join(text_parts), tool_titles


# ── Seam 1 + 2: ListMcpResources ─────────────────────────────────────────────


@pytest.mark.e2e
def test_list_mcp_resources(resources_kernel: tuple[int, str]) -> None:
    """LLM calls ListMcpResources and receives the real server's resource list.

    Proves Seam 1 (MCPManager reaches OrchestratorDeps) and Seam 2
    (OrchestratorDeps.mcp reaches ToolContext.mcp_manager) by verifying
    that the tool result contains URIs from the real mcp_resources_server.
    """
    port, token = resources_kernel
    if not _llm_available(port, token):
        pytest.skip("No LLM configured")

    text, tool_titles = _run(_run_prompt(
        port, token,
        "Use the ListMcpResources tool to list all available MCP resources "
        "from the 'resources' server, then tell me what URIs you found.",
    ))

    # Tool was called.
    assert any("ListMcpResources" in t or "listMcpResources" in t.lower() for t in tool_titles), (
        f"Expected ListMcpResources tool call, got: {tool_titles}"
    )

    # Response mentions real URIs from mcp_resources_server.py.
    assert "notes://daily/today" in text or "notes://" in text, (
        f"Expected notes:// URI in response, got: {text[:300]!r}"
    )
    assert "config://app/settings" in text or "config://" in text, (
        f"Expected config:// URI in response, got: {text[:300]!r}"
    )


# ── Seam 1 + 2: ReadMcpResource ──────────────────────────────────────────────


@pytest.mark.e2e
def test_read_mcp_resource_text(resources_kernel: tuple[int, str]) -> None:
    """LLM calls ReadMcpResource and receives real text content.

    Proves both seams: the tool reaches the live MCPManager which issues
    resources/read to the real mcp_resources_server subprocess.
    """
    port, token = resources_kernel
    if not _llm_available(port, token):
        pytest.skip("No LLM configured")

    text, tool_titles = _run(_run_prompt(
        port, token,
        "Use the ReadMcpResource tool to read the resource at URI "
        "'config://app/settings' from the 'resources' MCP server. "
        "Tell me what the 'version' field says.",
    ))

    # Tool was called.
    assert any("ReadMcpResource" in t or "readMcpResource" in t.lower() for t in tool_titles), (
        f"Expected ReadMcpResource tool call, got: {tool_titles}"
    )

    # Response contains real content from the server.
    assert "0.1.0" in text, (
        f"Expected version '0.1.0' from config resource, got: {text[:300]!r}"
    )


@pytest.mark.e2e
def test_read_mcp_resource_blob(resources_kernel: tuple[int, str]) -> None:
    """LLM calls ReadMcpResource on a binary (blob) resource.

    Verifies the blob-to-disk path: the tool should decode the base64 PNG,
    save it, and return a file path rather than raw base64 in the context.
    """
    port, token = resources_kernel
    if not _llm_available(port, token):
        pytest.skip("No LLM configured")

    text, tool_titles = _run(_run_prompt(
        port, token,
        "Use the ReadMcpResource tool to read the resource at URI "
        "'image://logo/png' from the 'resources' MCP server. "
        "Tell me whether you received a file path or raw base64 data.",
    ))

    assert any("ReadMcpResource" in t or "readMcpResource" in t.lower() for t in tool_titles), (
        f"Expected ReadMcpResource tool call, got: {tool_titles}"
    )

    # The blob should have been saved to disk — LLM should mention a file path.
    assert "saved" in text.lower() or "/tmp" in text or ".png" in text, (
        f"Expected blob-saved-to-disk message in response, got: {text[:300]!r}"
    )
