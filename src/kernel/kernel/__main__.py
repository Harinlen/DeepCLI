"""Allow running kernel as ``python -m kernel``."""

import argparse


def main() -> None:
    """Entry point for ``python -m kernel``."""
    parser = argparse.ArgumentParser(description="Mustang Kernel")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--port", type=int, required=True, help="Port to listen on")
    parser.add_argument("--dev", action="store_true", help="Enable INFO-level logging")
    args = parser.parse_args()

    if args.version:
        from kernel import __version__

        print(f"mustang kernel {__version__}")
        return

    import os

    import uvicorn

    if args.dev:
        os.environ["_MUSTANG_DEV"] = "1"

    uvicorn.run(
        "kernel.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=args.port,
        log_level="info" if args.dev else "warning",
        reload=False,
        # Heartbeat is delegated to uvicorn's native WebSocket
        # ping/pong — see docs/subsystems/transport.md §Heartbeat.
        # 20s interval / 20s timeout = a dead peer is dropped
        # within ~40s, which is responsive enough for IDE clients
        # and gentle enough to not flood an idle connection.
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
    )


if __name__ == "__main__":
    main()
