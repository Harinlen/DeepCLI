"""Live probe: ask the LLM "what tools do you have?" and print its answer.

Starts a fresh kernel subprocess against the user's real ~/.mustang/
config (NOT the e2e sandbox), connects via ACP, sends one prompt, and
prints the raw assistant reply plus any tool calls observed.

Goal: verify the deferred-tool listing reaches the LLM and that the
model knows about WebSearch / WebFetch.

Run:
    cd /home/saki/Documents/truenorth/mustang
    .venv/bin/python tests/probe/probe_what_tools.py
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "probe"))

from probe.client import (  # noqa: E402
    AgentChunk,
    PermissionRequest,
    ProbeClient,
    ToolCallEvent,
    TurnComplete,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_kernel(port: int, timeout: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1.0)
            return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    raise RuntimeError(f"kernel did not come up on port {port} in {timeout}s")


async def run_query(port: int, token: str, prompt: str) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    tool_titles: list[str] = []
    async with ProbeClient(port=port, token=token, request_timeout=120.0) as client:
        await client.initialize()
        sid = await client.new_session()
        async for ev in client.prompt(sid, prompt, timeout=120.0):
            if isinstance(ev, AgentChunk):
                text_parts.append(ev.text)
            elif isinstance(ev, ToolCallEvent):
                tool_titles.append(ev.title)
            elif isinstance(ev, PermissionRequest):
                await client.reply_permission(ev.req_id, "allow_once")
            elif isinstance(ev, TurnComplete):
                pass
    return "".join(text_parts), tool_titles


def main() -> int:
    port = _free_port()
    env = os.environ.copy()
    # Use the user's real ~/.mustang config (bedrock provider).
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "kernel", "--port", str(port)],
        env=env,
        cwd="/home/saki/Documents/truenorth/mustang/src/kernel",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_kernel(port)
        token_path = Path.home() / ".mustang" / "state" / "auth_token"
        token = token_path.read_text().strip()

        prompt = "你现在有什么工具？请把所有可用的工具都列出来，包括需要 ToolSearch 加载的延迟工具。"
        text, tool_titles = asyncio.run(run_query(port, token, prompt))

        print("=" * 70)
        print("PROMPT:", prompt)
        print("=" * 70)
        print("TOOL CALLS:", tool_titles)
        print("=" * 70)
        print("ASSISTANT REPLY:")
        print(text)
        print("=" * 70)

        # Did the LLM mention WebSearch / WebFetch?
        ok_search = "WebSearch" in text
        ok_fetch = "WebFetch" in text
        print(f"WebSearch mentioned in reply: {ok_search}")
        print(f"WebFetch  mentioned in reply: {ok_fetch}")
        return 0 if (ok_search and ok_fetch) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
