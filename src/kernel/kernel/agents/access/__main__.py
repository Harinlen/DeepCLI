"""Standalone Access Agent process entrypoint."""

from __future__ import annotations

import argparse
import os

import uvicorn

from kernel.uvicorn_runtime import uvicorn_loop


def main() -> None:
    parser = argparse.ArgumentParser(description="Mustang Access Agent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--hub-endpoint", required=True)
    parser.add_argument("--prompt-backend", choices=("compat", "router"), default="router")
    parser.add_argument("--supervisor-control-socket")
    parser.add_argument("--supervisor-control-token")
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()

    os.environ["MUSTANG_AGENT_HUB_ENDPOINT"] = args.hub_endpoint
    if args.supervisor_control_socket:
        os.environ["MUSTANG_SUPERVISOR_CONTROL_SOCKET"] = args.supervisor_control_socket
    if args.supervisor_control_token:
        os.environ["MUSTANG_SUPERVISOR_CONTROL_TOKEN"] = args.supervisor_control_token
    if args.prompt_backend == "router":
        os.environ["MUSTANG_AGENT_PROMPT_BACKEND"] = "router"
    else:
        os.environ.pop("MUSTANG_AGENT_PROMPT_BACKEND", None)
    if args.dev:
        os.environ["_MUSTANG_DEV"] = "1"

    uvicorn.run(
        "kernel.agents.access.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info" if args.dev else "warning",
        loop=uvicorn_loop(),
        reload=False,
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
    )


if __name__ == "__main__":
    main()
