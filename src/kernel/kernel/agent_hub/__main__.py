"""Standalone Agent Hub process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess  # nosec B404
import sys
from pathlib import Path

from kernel.agent_hub import AgentHub, AgentHubManager, AgentHubWebSocketServer
from kernel.agent_hub.contracts import default_primary_agent_definition


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="Mustang Agent Hub")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--runtime-file", required=True)
    parser.add_argument("--primary-token", required=True)
    parser.add_argument("--access-router-endpoint")
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--workspace", default=str(Path.cwd()))
    args = parser.parse_args()

    definition = default_primary_agent_definition(
        home=args.home,
        workspace=args.workspace,
    )
    hub = AgentHub(manager=AgentHubManager([definition]))
    hub.router.update_snapshot(hub.manager.routing_snapshot(revision=1))
    server = AgentHubWebSocketServer(
        hub,
        registration_tokens={"primary": args.primary_token},
        host=args.host,
        port=args.port,
    )
    await server.start()
    primary_proc = _start_primary_runtime(
        agent_id="primary",
        host=args.host,
        hub_endpoint=server.endpoint,
        access_router_endpoint=args.access_router_endpoint,
        registration_token=args.primary_token,
        home=Path(args.home),
        workspace=Path(args.workspace),
        runtime_file=Path(args.runtime_file).with_name("primary-agent.json"),
    )
    _write_json(
        Path(args.runtime_file),
        {
            "pid": os.getpid(),
            "endpoint": server.endpoint,
            "ready": True,
            "role": "agent_hub",
            "managedRuntimes": {"primary": {"pid": primary_proc.pid}},
        },
    )
    try:
        await asyncio.Event().wait()
    finally:
        _terminate(primary_proc)
        await server.stop()


def _start_primary_runtime(
    *,
    agent_id: str,
    host: str,
    hub_endpoint: str,
    access_router_endpoint: str | None,
    registration_token: str,
    home: Path,
    workspace: Path,
    runtime_file: Path,
) -> subprocess.Popen[bytes]:
    state_dir = home / "agents" / agent_id
    session_store = state_dir / "sessions" / "sessions.db"
    port = _free_port(host)
    command = [
        sys.executable,
        "-m",
        "kernel.agents.mustang.runtime",
        "--agent-id",
        agent_id,
        "--host",
        host,
        "--port",
        str(port),
        *(["--hub-endpoint", hub_endpoint] if access_router_endpoint is None else []),
        *(["--access-router-endpoint", access_router_endpoint] if access_router_endpoint else []),
        f"--registration-token={registration_token}",
        "--state-dir",
        str(state_dir),
        "--session-store-path",
        str(session_store),
        "--workspace",
        str(workspace),
        "--runtime-file",
        str(runtime_file),
    ]
    env = os.environ.copy()
    env["MUSTANG_AGENT_ID"] = agent_id
    if access_router_endpoint:
        try:
            env["MUSTANG_ACCESS_PORT"] = access_router_endpoint.rsplit(":", 1)[1]
        except IndexError:
            pass
    return subprocess.Popen(command, env=env)  # nosec B603


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
