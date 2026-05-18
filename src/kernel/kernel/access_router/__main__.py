"""Access Router process entrypoint."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import uvicorn

from kernel.access_router.app import create_app
from kernel.uvicorn_runtime import uvicorn_loop


def main() -> None:
    parser = argparse.ArgumentParser(description="Mustang Access Router")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--runtime-file")
    parser.add_argument("--hub-endpoint")
    parser.add_argument("--resource-home")
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()

    if args.hub_endpoint:
        os.environ["MUSTANG_AGENT_HUB_ENDPOINT"] = args.hub_endpoint
    if args.dev:
        os.environ["_MUSTANG_DEV"] = "1"
    if args.runtime_file:
        _write_runtime_file(Path(args.runtime_file), args.host, args.port)

    uvicorn.run(
        create_app(resource_home=args.resource_home),
        host=args.host,
        port=args.port,
        log_level="info",
        loop=uvicorn_loop(),
        factory=False,
        ws_ping_interval=20.0,
    )


def _write_runtime_file(path: Path, host: str, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ready": True,
        "role": "access_router",
        "pid": os.getpid(),
        "endpoint": f"http://{host}:{port}",
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
