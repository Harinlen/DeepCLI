"""Standalone Supervisor entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

from kernel.supervisor.runtime import (
    SupervisorConfig,
    SupervisorRuntime,
    install_signal_handlers,
)
from kernel.core.paths import user_state_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Mustang Supervisor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--access-port", type=int, default=8200)
    parser.add_argument("--state-dir", default=str(user_state_dir()))
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--prompt-backend", choices=("compat", "router"), default="router")
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()

    runtime = SupervisorRuntime(
        SupervisorConfig(
            access_port=args.access_port,
            state_dir=Path(args.state_dir),
            workspace=Path(args.workspace),
            host=args.host,
            dev=args.dev,
            prompt_backend=args.prompt_backend,
        )
    )
    install_signal_handlers(runtime)
    try:
        runtime.start()
        runtime.wait()
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
