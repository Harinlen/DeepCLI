"""Standalone Agent Hub process entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from kernel.agent_hub import AgentHub, AgentHubManager, AgentHubWebSocketServer
from kernel.agent_hub.contracts import default_primary_agent_definition


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="Mustang Agent Hub")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--runtime-file", required=True)
    parser.add_argument("--primary-token", required=True)
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
    _write_json(
        Path(args.runtime_file),
        {
            "pid": os.getpid(),
            "endpoint": server.endpoint,
            "ready": True,
            "role": "agent_hub",
        },
    )
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
